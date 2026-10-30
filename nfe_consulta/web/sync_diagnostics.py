"""Metadados de erros de sincronização, sem mensagens brutas ou conteúdo fiscal."""
from datetime import datetime
import re
from nfe_consulta.janela_consulta import janela_atual
import subprocess

from nfe_consulta.janela_consulta import BRASILIA
from nfe_consulta.modelos import NfeConsumoIndevidoErro, NfeErroComunicacao, NfeErroCertificado


def nsu_valido(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 15:
        return value.zfill(15)
    return None


def detalhes_erro(exc, contexto=None):
    """Allowlist compartilhada pelo log técnico e histórico visível."""
    dados = contexto if contexto is not None else getattr(exc, "sync_diagnostics", {})
    resultado = {}
    if not isinstance(dados, dict):
        dados = {}
    if type(dados.get("recuperacao_cons_nsu")) is bool:
        resultado["recuperacao_cons_nsu"] = dados["recuperacao_cons_nsu"]
    execucao = dados.get("execucao")
    if isinstance(execucao, str) and re.fullmatch(r"[a-f0-9]{32}", execucao):
        resultado["execucao"] = execucao
    etapa = "certificado" if isinstance(exc, NfeErroCertificado) else dados.get("etapa")
    if isinstance(etapa, str) and etapa in {"janela", "banco", "certificado", "sincronizacao", "distNSU", "consNSU"}:
        resultado["etapa"] = etapa
    for chave in ("nsu_antes", "nsu_salvo", "nsu_solicitado", "max_nsu", "nsu_sefaz"):
        value = nsu_valido(dados.get(chave))
        if value is not None:
            resultado[chave] = value
    if isinstance(exc, NfeConsumoIndevidoErro):
        resultado["cstat"] = 656
        enviado = nsu_valido(exc.ult_nsu_enviado)
        if enviado is not None:
            resultado.update(nsu_solicitado=enviado, etapa="distNSU")
        remoto = nsu_valido(exc.ult_nsu)
        if remoto is not None:
            resultado["nsu_sefaz"] = remoto
    elif type(dados.get("cstat")) is int and 100 <= dados["cstat"] <= 999:
        resultado["cstat"] = dados["cstat"]
    if isinstance(exc, NfeErroComunicacao) and not isinstance(exc, NfeConsumoIndevidoErro):
        if isinstance(exc.__cause__, subprocess.TimeoutExpired) or "timeout" in str(exc).lower():
            resultado["falha_comunicacao"] = "timeout"
        elif "resposta vazia" in str(exc).lower():
            resultado["falha_comunicacao"] = "resposta_vazia"
        else:
            http = re.search(r"\bHTTP ([1-5][0-9]{2})\b", str(exc), re.IGNORECASE)
            if http:
                resultado["http_status"] = int(http[1])
    if isinstance(dados.get("falha_comunicacao"), str) and dados["falha_comunicacao"] in {"timeout", "resposta_vazia"}:
        resultado["falha_comunicacao"] = dados["falha_comunicacao"]
    if type(dados.get("http_status")) is int and 100 <= dados["http_status"] <= 599:
        resultado["http_status"] = dados["http_status"]
    for chave in ("lotes_gravados", "eventos_gravados", "respostas_pendentes", "recuperados_na_tentativa"):
        value = dados.get(chave)
        if type(value) is int and value >= 0:
            resultado[chave] = value
    duracao = dados.get("duracao_segundos")
    if type(duracao) in (int, float) and 0 <= duracao < 604800:
        resultado["duracao_segundos"] = round(duracao, 2)
    for chave in ("quando", "proxima_tentativa"):
        value = dados.get(chave)
        if isinstance(value, str):
            try:
                instante = datetime.fromisoformat(value)
                if instante.tzinfo is not None:
                    resultado[chave] = instante.astimezone(BRASILIA).isoformat()
            except (ValueError, OverflowError):
                pass
    return resultado


def atualizar_estado(settings, contexto, *, inicial=False):
    """Falha na leitura do diagnóstico nunca substitui o erro original."""
    from nfe_consulta.web.status_view import read_web_status
    from nfe_consulta.web.sync_runtime import caminho_banco_configurado
    from nfe_consulta.config import CNPJ_PADRAO
    try:
        status = read_web_status(caminho_banco_configurado(settings), CNPJ_PADRAO,
                                 password=settings.current_database_password())
        contexto.update(nsu_salvo=status.ult_nsu, max_nsu=status.max_nsu,
                        respostas_pendentes=status.pending_responses,
                        proxima_tentativa=status.available_at.isoformat() if status.available_at else None)
        if inicial:
            contexto["nsu_antes"] = status.ult_nsu
            contexto["recuperados_antes"] = status.recovery_received
        elif "recuperados_antes" in contexto:
            contexto["recuperados_na_tentativa"] = max(0, status.recovery_received - contexto["recuperados_antes"])
    except Exception:
        pass


def mensagem_erro(exc):
    """Texto público acionável; não inclui caminhos, senhas ou saída do PowerShell."""
    motivo = type(exc).__name__
    mensagens = {
        "NfeConsumoIndevidoErro": "A SEFAZ rejeitou a consulta por consumo indevido (656). Aguarde o fim da pausa; não altere o NSU para contornar o bloqueio.",
        "NfeJanelaConsultaErro": f"A janela de consultas terminou ou ainda não abriu. As consultas são permitidas das {janela_atual().rotulo} de Brasília.",
        "NfeLimiteConsultaErro": "O intervalo entre consultas ou a pausa da SEFAZ ainda está ativo. Aguarde a próxima tentativa permitida.",
        "NfeErroCertificado": "Não foi possível usar o certificado. Confira a validade, a senha e o acesso ao certificado no computador que executa o app.",
        "FileNotFoundError": "Banco, certificado ou componente necessário não encontrado. Confira os arquivos configurados no servidor.",
        "NfeErroComunicacao": "A consulta à SEFAZ não foi concluída. Confira a conexão e aguarde a próxima tentativa permitida.",
        "NfeErroResposta": "A resposta da SEFAZ não pôde ser processada. Confira as pendências antes de uma nova consulta.",
        "DatabaseError": "Não foi possível ler ou gravar o banco. Confira a senha, as permissões e o espaço em disco.",
        "OperationalError": "Não foi possível ler ou gravar o banco. Confira as permissões, o espaço em disco e se o banco está ocupado.",
        "IntegrityError": "A gravação foi interrompida. Confira as respostas pendentes antes de tentar novamente.",
    }
    texto = mensagens.get(motivo, "A sincronização foi interrompida. Consulte os logs e confira a configuração antes da próxima tentativa.")
    dados = detalhes_erro(exc)
    if dados.get("falha_comunicacao") == "timeout":
        texto += " O tempo de resposta foi excedido."
    if dados.get("falha_comunicacao") == "resposta_vazia":
        texto += " O serviço retornou uma resposta vazia."
    if dados.get("http_status"):
        texto += f" O serviço retornou HTTP {dados['http_status']}."
    if dados.get("nsu_salvo"):
        texto += f" Último NSU salvo: {dados['nsu_salvo']}."
    if dados.get("proxima_tentativa"):
        instante = datetime.fromisoformat(dados["proxima_tentativa"])
        texto += f" Próxima tentativa permitida: {instante:%d/%m/%Y %H:%M} (Brasília)."
    if dados.get("execucao"):
        texto += f" Referência: {dados['execucao']}."
    return texto
