"""Recuperação pontual de lacunas, sem alterar o cursor distNSU."""
from nfe_consulta.janela_consulta import exigir_janela
from dataclasses import replace
from nfe_consulta.distribuicao import consultar_nsu, preservar_resposta, _erro_consumo_indevido
from nfe_consulta.parser_distribuicao import parse_retorno_distribuicao
from nfe_consulta.modelos import NfeErroResposta


def recuperar_intervalos(banco, cnpj, uf, certificado, destinatarios=(), limite=20, estatisticas=None):
    if not 1 <= limite <= 20:
        raise ValueError('Limite de recuperação deve estar entre 1 e 20')
    if banco.pausa_ativa(cnpj):
        return 0
    inseridos = 0
    estatisticas = estatisticas if estatisticas is not None else {}
    estatisticas.update(documentos=0, manifestacoes=0, consultas=0)
    # Uma reserva sem resposta pode sobreviver à interrupção do processo.
    with banco.conexao:
        banco.conexao.execute("UPDATE consultas_nsu SET resultado='repetir' WHERE cnpj=? AND resultado='pendente' AND resposta_xml IS NULL AND tentado_em<datetime('now','-1 hour')", (cnpj,))
        banco.conexao.execute(
            "UPDATE fila_nsu_prioridade SET status='pendente', atualizado_em=CURRENT_TIMESTAMP "
            "WHERE cnpj=? AND status='consultando' AND atualizado_em<datetime('now','-1 hour')", (cnpj,))

    def finalizar_prioridade(nsu, status):
        with banco.conexao:
            banco.conexao.execute(
                "UPDATE fila_nsu_prioridade SET status=?, atualizado_em=CURRENT_TIMESTAMP "
                "WHERE cnpj=? AND nsu=? AND status IN ('consultando','pendente','erro')",
                (status, cnpj, nsu))

    def processar(identificador, nsu, retorno):
        nonlocal inseridos
        if retorno.status_codigo == 656:
            exc = _erro_consumo_indevido(retorno)
            banco.pausar_distribuicao(cnpj, str(exc))
            with banco.conexao:
                banco.conexao.execute("UPDATE consultas_nsu SET resultado='repetir',resposta_xml=NULL WHERE id=?", (identificador,))
            finalizar_prioridade(nsu, 'erro')
            raise exc
        if retorno.status_codigo == 137:
            if retorno.documentos:
                raise NfeErroResposta('Resposta 137 com documentos; intervalo permanece pendente')
            with banco.conexao:
                banco.conexao.execute("UPDATE consultas_nsu SET resultado='indisponivel',resposta_xml=NULL WHERE id=?", (identificador,))
            finalizar_prioridade(nsu, 'indisponivel')
            return
        if retorno.status_codigo != 138 or len(retorno.documentos) != 1 or retorno.documentos[0][0] != nsu:
            raise NfeErroResposta('Resposta consNSU inesperada; intervalo permanece pendente')
        atual, maximo = banco.obter_estado(cnpj)
        antes = inseridos
        inseridos += banco.salvar_retorno(cnpj, replace(retorno, ult_nsu=atual, max_nsu=maximo),
                                         destinatarios, atualizar_estado=False, consulta_nsu_id=identificador)
        estatisticas['documentos'] += len(retorno.documentos)
        estatisticas['manifestacoes'] += inseridos - antes
        finalizar_prioridade(nsu, 'recebido')

    for identificador, nsu, xml in banco.conexao.execute(
        "SELECT id,nsu,resposta_xml FROM consultas_nsu WHERE cnpj=? AND resultado='pendente' AND resposta_xml IS NOT NULL ORDER BY id",
        (cnpj,)).fetchall():
        processar(identificador, nsu, parse_retorno_distribuicao(xml))

    for _ in range(limite):
        exigir_janela()
        banco.conexao.execute('BEGIN IMMEDIATE')
        try:
            usadas = banco.conexao.execute(
                "SELECT COUNT(*) FROM consultas_nsu WHERE cnpj=? AND tentado_em>=datetime('now','-1 hour')", (cnpj,)).fetchone()[0]
            if usadas + banco.consultas_na_ultima_hora(cnpj) >= 20:
                banco.conexao.rollback()
                break
            # A fila alta passa à frente das lacunas; a normal vem depois.
            nsu_alto = banco.proximo_nsu_prioritario(cnpj, alta=True)
            nsu = nsu_alto or banco.proximo_nsu_faltante(cnpj)
            nsu = nsu or banco.proximo_nsu_prioritario(cnpj, alta=False)
            if nsu is None:
                banco.conexao.rollback()
                break
            banco.conexao.execute(
                "UPDATE fila_nsu_prioridade SET status='consultando', atualizado_em=CURRENT_TIMESTAMP "
                "WHERE cnpj=? AND nsu=? AND status='pendente'", (cnpj, nsu))
            identificador = banco.conexao.execute(
                'INSERT INTO consultas_nsu(cnpj,nsu) VALUES (?,?)', (cnpj,nsu)).lastrowid
            banco.conexao.commit()
        except Exception:
            banco.conexao.rollback()
            raise

        def guardar(xml):
            with banco.conexao:
                banco.conexao.execute('UPDATE consultas_nsu SET resposta_xml=? WHERE id=?', (xml,identificador))
        try:
            estatisticas["consultas"] += 1
            with preservar_resposta(guardar):
                retorno = consultar_nsu(cnpj, uf, nsu, certificado)
        except Exception:
            with banco.conexao:
                banco.conexao.execute("UPDATE consultas_nsu SET resultado='repetir' WHERE id=? AND resposta_xml IS NULL", (identificador,))
            finalizar_prioridade(nsu, 'erro')
            raise
        processar(identificador, nsu, retorno)
    return inseridos
