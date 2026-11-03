import xml.etree.ElementTree as ET

from nfe_consulta.carta_correcao import read_corrections
from nfe_consulta.modelos import Manifestacao, ResultadoConsulta

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

DESCRICOES = {
    "210200": "Confirmacao da Operacao",
    "210210": "Ciencia da Operacao",
    "210220": "Desconhecimento da Operacao",
    "210240": "Operacao nao Realizada",
}


def _find_text(el, xpath, default=""):
    node = el.find(xpath, NS)
    return node.text if node is not None else default


def parse_retorno_consulta(xml_str: str, chave: str) -> ResultadoConsulta:
    root = ET.fromstring(xml_str)

    status = int(_find_text(root, ".//nfe:cStat", "0"))
    motivo = _find_text(root, ".//nfe:xMotivo")
    protocolo = _find_text(root, ".//nfe:nProt") or None

    manifestacoes = []
    for evento in root.findall(".//nfe:procEventoNFe", NS):
        tp = _find_text(evento, ".//nfe:tpEvento")
        if tp == '110110':
            try:
                manifestacoes.extend(event for key, event in read_corrections(evento) if key == chave)
            except ValueError:
                pass
        elif tp in DESCRICOES:
            desc = DESCRICOES[tp]
            data = _find_text(evento, ".//nfe:dhEvento")
            prot = _find_text(evento, ".//nfe:nProt")
            manifestacoes.append(Manifestacao(tp, desc, data, prot))

    return ResultadoConsulta(
        chave=chave,
        status_codigo=status,
        status_motivo=motivo,
        protocolo_nfe=protocolo,
        manifestacoes=tuple(manifestacoes),
        cancelada=status in (101, 151),
    )
