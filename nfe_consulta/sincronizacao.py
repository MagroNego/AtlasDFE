from nfe_consulta.janela_consulta import exigir_janela
from dataclasses import dataclass
from typing import Callable

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.distribuicao import consultar_distribuicao, preservar_resposta, _erro_consumo_indevido
from nfe_consulta.parser_distribuicao import parse_retorno_distribuicao
from nfe_consulta.modelos import CertificadoWindows, NfeErroComunicacao, NfeConsumoIndevidoErro


@dataclass(frozen=True)
class ResumoSincronizacao:
    lotes: int
    eventos_novos: int
    documentos_ignorados: int
    ult_nsu: str
    max_nsu: str
    completo: bool
    cache: bool = False
    documentos_atuais: int = 0
    manifestacoes_atuais: int = 0
    documentos_recuperados: int = 0
    manifestacoes_recuperadas: int = 0
    somente_recuperacao: bool = False


def sincronizar(
    banco: BancoManifestacoes,
    cnpj: str,
    c_uf_autor: str,
    certificado: CertificadoWindows,
    max_lotes: int = 50,
    progresso_fn: Callable | None = None,
    destinatarios_alerta: tuple[str, ...] = (),
    recuperar_lacunas: bool = False,
) -> ResumoSincronizacao:
    if max_lotes < 1:
        raise ValueError("max_lotes deve ser maior que zero")
    lotes = 0
    eventos_novos = 0
    ignorados = 0
    documentos_atuais = 0
    recuperados = {}

    def rejeitar(exc, enviado, resposta_id=None):
        with banco.conexao:
            banco.registrar_recuperacao_nsu(cnpj, enviado, exc.ult_nsu)
            if resposta_id is not None:
                banco.finalizar_resposta(resposta_id, "656", str(exc))
            banco.pausar_distribuicao(cnpj, str(exc))

    # Reaproveita a resposta recebida, mesmo após reinício/erro de gravação.
    # Um XML inválido permanece pendente e bloqueia avanços silenciosos.
    for resposta_id, enviado, xml in banco.respostas_pendentes(cnpj):
        retorno = parse_retorno_distribuicao(xml)
        if retorno.status_codigo == 656:
            exc = _erro_consumo_indevido(retorno, ult_nsu_enviado=enviado)
            rejeitar(exc, enviado, resposta_id)
            raise exc
        if retorno.status_codigo not in (137, 138):
            with banco.conexao:
                banco.finalizar_resposta(resposta_id, str(retorno.status_codigo), retorno.status_motivo)
            raise NfeErroComunicacao(f"SEFAZ retornou {retorno.status_codigo}: {retorno.status_motivo}")
        if (int(retorno.ult_nsu) < int(enviado)
                or (retorno.status_codigo == 138 and retorno.ult_nsu == enviado)
                or (retorno.status_codigo == 137 and retorno.ult_nsu != retorno.max_nsu)):
            raise NfeErroComunicacao("Resposta pendente com NSUs inconsistentes; cursor nao foi salvo")
        eventos_novos += banco.salvar_retorno(
            cnpj, retorno, destinatarios_alerta, resposta_id=resposta_id)
        lotes += 1
        ignorados += retorno.documentos_ignorados
        documentos_atuais += len(retorno.documentos)

    ult_nsu, max_nsu = banco.obter_estado(cnpj)
    pausa = banco.pausa_ativa(cnpj)
    if pausa:
        raise NfeErroComunicacao(pausa)
    # Isola a recuperação: uma nova divergência no distNSU não deve impedir
    # o recebimento das pendências. O cursor de distribuição permanece intacto.
    if recuperar_lacunas and (banco.proximo_nsu_prioritario(cnpj) is not None
            or banco.proximo_nsu_faltante(cnpj) is not None
            or banco.conexao.execute(
                "SELECT 1 FROM consultas_nsu WHERE cnpj=? AND resultado='pendente' LIMIT 1",
                (cnpj,)).fetchone()):
        from nfe_consulta.recuperacao_nsu import recuperar_intervalos
        eventos_novos += recuperar_intervalos(
            banco, cnpj, c_uf_autor, certificado, destinatarios_alerta,
            estatisticas=recuperados)
        return ResumoSincronizacao(
            lotes, eventos_novos, ignorados, ult_nsu, max_nsu, False,
            cache=not lotes and not recuperados.get('consultas') and not recuperados.get('documentos'),
            documentos_atuais=documentos_atuais,
            manifestacoes_atuais=eventos_novos-recuperados.get('manifestacoes', 0),
            documentos_recuperados=recuperados.get('documentos', 0),
            manifestacoes_recuperadas=recuperados.get('manifestacoes', 0),
            somente_recuperacao=True)
    if (banco.nsu_para_consulta(cnpj) == ult_nsu
            and banco.sincronizacao_recente_e_completa(cnpj)):
        if recuperar_lacunas:
            from nfe_consulta.recuperacao_nsu import recuperar_intervalos
            eventos_novos += recuperar_intervalos(banco, cnpj, c_uf_autor, certificado, destinatarios_alerta, estatisticas=recuperados)
        return ResumoSincronizacao(lotes, eventos_novos, ignorados, ult_nsu, max_nsu, True,
                                  not lotes and not recuperados.get('consultas') and not recuperados.get('documentos'),
                                  documentos_atuais, eventos_novos-recuperados.get('manifestacoes',0),
                                  recuperados.get('documentos',0), recuperados.get('manifestacoes',0))
    completo = False

    while lotes < max_lotes:
        exigir_janela()
        anterior = banco.nsu_para_consulta(cnpj)
        resposta_id = None

        def guardar(xml):
            nonlocal resposta_id
            resposta_id = banco.preservar_resposta(cnpj, anterior, xml)

        try:
            with preservar_resposta(guardar):
                retorno = consultar_distribuicao(cnpj, c_uf_autor, anterior, certificado)
        except NfeConsumoIndevidoErro as exc:
            rejeitar(exc, anterior, resposta_id)
            raise
        except Exception as exc:
            if resposta_id is not None:
                with banco.conexao:
                    banco.conexao.execute(
                        "UPDATE respostas_distribuicao SET detalhe=? WHERE id=?",
                        (f"{type(exc).__name__}: {str(exc)[:800]}", resposta_id),
                    )
            raise
        if retorno.status_codigo == 137 and retorno.ult_nsu != retorno.max_nsu:
            raise NfeErroComunicacao("Resposta 137 com NSUs inconsistentes; cursor nao foi salvo")
        if int(retorno.ult_nsu) < int(anterior) or (retorno.status_codigo == 138 and retorno.ult_nsu == anterior):
            raise NfeErroComunicacao("A SEFAZ nao avancou o ultNSU; sincronizacao interrompida")
        lotes += 1
        eventos_novos += banco.salvar_retorno(cnpj, retorno, destinatarios_alerta, resposta_id=resposta_id)
        ignorados += retorno.documentos_ignorados
        documentos_atuais += len(retorno.documentos)
        ult_nsu, max_nsu = retorno.ult_nsu, retorno.max_nsu

        if progresso_fn:
            progresso_fn(lotes, ult_nsu, max_nsu, eventos_novos)

        if retorno.status_codigo == 137 or ult_nsu == max_nsu:
            completo = True
            break

    if recuperar_lacunas and completo:
        from nfe_consulta.recuperacao_nsu import recuperar_intervalos
        eventos_novos += recuperar_intervalos(banco, cnpj, c_uf_autor, certificado, destinatarios_alerta, estatisticas=recuperados)

    return ResumoSincronizacao(
        lotes=lotes,
        eventos_novos=eventos_novos,
        documentos_ignorados=ignorados,
        ult_nsu=ult_nsu,
        max_nsu=max_nsu,
        completo=completo,
        documentos_atuais=documentos_atuais,
        manifestacoes_atuais=eventos_novos-recuperados.get('manifestacoes',0),
        documentos_recuperados=recuperados.get('documentos',0),
        manifestacoes_recuperadas=recuperados.get('manifestacoes',0),
    )
