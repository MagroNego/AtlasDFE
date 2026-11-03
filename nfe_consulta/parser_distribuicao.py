import base64
import gzip
import io
from xml.etree import ElementTree as StdET
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from nfe_consulta.modelos import (
    InformacaoNota,
    Manifestacao,
    NfeErroResposta,
    RetornoDistribuicao,
)
from nfe_consulta.carta_correcao import read_corrections
from nfe_consulta.parser import DESCRICOES
from nfe_consulta.cte import parse_event
from nfe_consulta.cancelamento import read_cancellation

MAX_SOAP_BYTES = 32 * 1024 * 1024
MAX_DOCZIP_BYTES = 2 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024


def _nome_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _primeiro_texto(root: StdET.Element, *nomes: str) -> str:
    for nome in nomes:
        for elemento in root.iter():
            if _nome_local(elemento.tag) == nome and elemento.text:
                return elemento.text.strip()
    return ""


def _encontrar_retorno(root: StdET.Element) -> StdET.Element:
    if _nome_local(root.tag) == "retDistDFeInt":
        return root
    for elemento in root.iter():
        if _nome_local(elemento.tag) == "retDistDFeInt":
            return elemento
        if elemento.text and "<retDistDFeInt" in elemento.text:
            try:
                return ET.fromstring(elemento.text)
            except (StdET.ParseError, DefusedXmlException):
                pass
    raise NfeErroResposta("Resposta da SEFAZ sem retDistDFeInt")


def parse_documento_distribuido(
    xml_bytes: bytes, nsu: str = "", schema: str = ""
) -> tuple[str, Manifestacao] | None:
    if len(xml_bytes) > MAX_XML_BYTES:
        raise NfeErroResposta(f"XML distribuido excede limite de tamanho no NSU {nsu}")
    try:
        root = ET.fromstring(xml_bytes)
    except (StdET.ParseError, DefusedXmlException) as exc:
        raise NfeErroResposta(f"XML distribuido invalido no NSU {nsu}: {exc}") from exc

    if _nome_local(root.tag) not in ("resEvento", "procEventoNFe"):
        return None
    codigo = _primeiro_texto(root, "tpEvento")
    if codigo == '110110':
        try:
            return read_corrections(root, nsu=nsu, schema=schema, allow_summary=True)[0]
        except (ValueError, IndexError) as exc:
            raise NfeErroResposta(f'CC-e inválida no NSU {nsu}') from exc
    if codigo not in DESCRICOES:
        return None

    chave = _primeiro_texto(root, "chNFe")
    if len(chave) != 44 or not chave.isdigit():
        raise NfeErroResposta(f"Evento do NSU {nsu} sem chave NF-e valida")

    manifestacao = Manifestacao(
        codigo=codigo,
        descricao=DESCRICOES[codigo],
        data=_primeiro_texto(root, "dhRegEvento", "dhEvento"),
        protocolo=_primeiro_texto(root, "nProt"),
        nsu=nsu,
        schema=schema,
    )
    return chave, manifestacao


def _informacao_nota(xml_bytes: bytes) -> InformacaoNota | None:
    """Aproveita só emitente e cancelamento dos documentos distribuídos."""
    root = ET.fromstring(xml_bytes)
    tipo = _nome_local(root.tag)
    if tipo == "resNFe":
        chave = _primeiro_texto(root, "chNFe")
        emitente = _primeiro_texto(root, "xNome")[:100]
        cancelada = _primeiro_texto(root, "cSitNFe") == "3"
    elif tipo == "nfeProc":
        inf_nfe = next((e for e in root.iter() if _nome_local(e.tag) == "infNFe"), None)
        chave = inf_nfe.attrib.get("Id", "").removeprefix("NFe") if inf_nfe is not None else ""
        emit = next((e for e in root.iter() if _nome_local(e.tag) == "emit"), None)
        emitente = _primeiro_texto(emit, "xNome")[:100] if emit is not None else ""
        try:
            cancelada = read_cancellation(root, chave[6:20]) is not None
        except ValueError:
            return None
    elif tipo in {"resEvento", "procEventoNFe"} and _primeiro_texto(root, "tpEvento") == "110111":
        chave = _primeiro_texto(root, "chNFe")
        emitente = ""
        if tipo == 'procEventoNFe':
            try:
                cancelada = read_cancellation(root, chave[6:20]) is not None
            except ValueError:
                return None
        else:
            cancelada = True
    else:
        return None
    if len(chave) != 44 or not chave.isdigit():
        return None
    return InformacaoNota(chave, emitente, cancelada)


def parse_retorno_distribuicao(xml_str: str) -> RetornoDistribuicao:
    if len(xml_str.encode("utf-8")) > MAX_SOAP_BYTES:
        raise NfeErroResposta("Resposta da SEFAZ excede limite de tamanho")
    try:
        envelope = ET.fromstring(xml_str)
    except (StdET.ParseError, DefusedXmlException) as exc:
        raise NfeErroResposta(f"Resposta XML invalida da SEFAZ: {exc}") from exc

    root = _encontrar_retorno(envelope)
    status_texto = _primeiro_texto(root, "cStat") or "0"
    try:
        status = int(status_texto)
    except ValueError as exc:
        raise NfeErroResposta(f"cStat invalido na resposta: {status_texto}") from exc

    manifestacoes = []
    informacoes_notas = []
    eventos_cte = []
    ignorados = 0
    total_documentos = 0
    documentos = []
    for doc_zip in root.iter():
        if _nome_local(doc_zip.tag) != "docZip":
            continue
        total_documentos += 1
        if total_documentos > 50:
            raise NfeErroResposta("Resposta da SEFAZ com mais de 50 documentos")
        nsu = doc_zip.attrib.get("NSU", "")
        schema = doc_zip.attrib.get("schema", "")
        if not nsu.isascii() or not nsu.isdigit() or len(nsu) > 15:
            raise NfeErroResposta("docZip sem NSU valido")
        if any(d[0] == nsu.zfill(15) for d in documentos):
            raise NfeErroResposta("NSU repetido no lote recebido")
        try:
            texto_base64 = "".join((doc_zip.text or "").split())
            if len(texto_base64) > ((MAX_DOCZIP_BYTES + 2) // 3) * 4 + 4:
                raise NfeErroResposta(f"docZip excede limite de tamanho no NSU {nsu}")
            compactado = base64.b64decode(texto_base64, validate=True)
            with gzip.GzipFile(fileobj=io.BytesIO(compactado)) as fonte:
                xml_documento = fonte.read(MAX_XML_BYTES + 1)
            if len(xml_documento) > MAX_XML_BYTES:
                raise NfeErroResposta(f"XML expandido excede limite no NSU {nsu}")
        except (ValueError, OSError) as exc:
            raise NfeErroResposta(f"docZip invalido no NSU {nsu}: {exc}") from exc

        evento = parse_documento_distribuido(xml_documento, nsu, schema)
        cte = parse_event(ET.fromstring(xml_documento), nsu, schema)
        if cte is not None:
            eventos_cte.append(cte)
        info = _informacao_nota(xml_documento)
        if info is not None:
            informacoes_notas.append(info)
        if evento is None:
            if info is None and cte is None:
                ignorados += 1
        else:
            manifestacoes.append(evento)

        documentos.append((nsu.zfill(15), schema, xml_documento,
                           "manifestacao" if evento else "cte" if cte else "nota" if info else "ignorado"))

    ult_nsu = _primeiro_texto(root, "ultNSU")
    max_nsu = _primeiro_texto(root, "maxNSU")
    if status in (137, 138) and (not ult_nsu.isdigit() or not max_nsu.isdigit()):
        raise NfeErroResposta("Resposta sem ultNSU/maxNSU validos")
    return RetornoDistribuicao(
        status_codigo=status,
        status_motivo=_primeiro_texto(root, "xMotivo"),
        ult_nsu=(ult_nsu or "0").zfill(15),
        max_nsu=(max_nsu or "0").zfill(15),
        manifestacoes=tuple(manifestacoes),
        documentos_ignorados=ignorados,
        ult_nsu_informado=bool(ult_nsu and ult_nsu.isdigit()),
        informacoes_notas=tuple(informacoes_notas),
        eventos_cte=tuple(eventos_cte),
        documentos=tuple(documentos),
    )
