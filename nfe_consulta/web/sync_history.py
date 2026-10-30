"""Histórico público e resumido das sincronizações no log de auditoria."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nfe_consulta.janela_consulta import BRASILIA
from nfe_consulta.web.sync_diagnostics import detalhes_erro
from nfe_consulta.web.log_retention import SYNC_RETENTION_DAYS, log_instant


_REASONS = {
    "fora_janela": "Fora da janela de consultas configurada (Brasília)",
    "NfeJanelaConsultaErro": "Fora da janela de consultas configurada (Brasília)",
    "consumo_indevido_656": "SEFAZ: consumo indevido (656)",
    "NfeConsumoIndevidoErro": "SEFAZ: consumo indevido (656)",
    "NfeLimiteConsultaErro": "Intervalo entre consultas ou pausa da SEFAZ ainda ativo",
    "DatabaseError": "Falha de leitura ou gravação do banco",
    "OperationalError": "Banco ocupado, sem permissão ou indisponível",
    "IntegrityError": "Gravação interrompida; conferir respostas pendentes",
    "cooldown": "Intervalo entre consultas ainda ativo",
    "database_unavailable": "Banco indisponível; confira a configuração antes de sincronizar",
    "confirmation_required": "Sincronização manual não confirmada",
    "sync_in_progress": "Outra sincronização em andamento",
    "FileNotFoundError": "Banco ou certificado não encontrado",
    "NfeErroCertificado": "Falha no certificado",
    "NfeErroComunicacao": "Falha de comunicação com a SEFAZ",
    "NfeErroResposta": "Resposta inválida da SEFAZ",
}


@dataclass(frozen=True)
class SyncHistoryItem:
    when: str
    origin: str
    result: str
    lots: int | None
    new_events: int | None
    detail: str
    current_documents: int | None = None
    current_events: int | None = None
    recovered_documents: int | None = None
    recovered_events: int | None = None
    diagnostic: str = ""
    next_attempt: str = ""
    reference: str = ""
    attempts: tuple[SyncHistoryItem, ...] = ()
    group_key: str = ""
    group_when: str = ""


_ACTIONS = {
    "consumo_indevido_656": "Aguarde a pausa e não altere o NSU para contornar a rejeição.",
    "NfeConsumoIndevidoErro": "Aguarde a pausa e não altere o NSU para contornar a rejeição.",
    "NfeErroCertificado": "Confira validade, senha e acesso ao certificado no servidor.",
    "FileNotFoundError": "Confira os arquivos configurados no servidor.",
    "NfeErroComunicacao": "Confira a conexão com a SEFAZ antes da próxima tentativa.",
    "NfeErroResposta": "Confira as respostas pendentes antes de uma nova consulta.",
    "DatabaseError": "Confira senha, permissões e espaço em disco.",
    "OperationalError": "Confira permissões, espaço em disco e se o banco está ocupado.",
    "IntegrityError": "Confira as respostas pendentes antes de tentar novamente.",
}


def _diagnostico(payload):
    # Reutiliza a allowlist; conteúdo bruto e dados internos são descartados.
    dados = detalhes_erro(Exception(), payload)
    partes = []
    etapas = {"janela": "Verificação de horário", "banco": "Abertura do banco",
              "certificado": "Configuração do certificado", "sincronizacao": "Sincronização",
              "distNSU": "Consulta de novos documentos (distNSU)", "consNSU": "Recuperação (consNSU)"}
    if dados.get("etapa"):
        partes.append(etapas[dados["etapa"]])
    if dados.get("falha_comunicacao") == "timeout":
        partes.append("Tempo de resposta excedido")
    if dados.get("falha_comunicacao") == "resposta_vazia":
        partes.append("Resposta vazia")
    if dados.get("http_status"):
        partes.append(f"HTTP: {dados['http_status']}")
    if dados.get("cstat"):
        partes.append(f"SEFAZ: {dados['cstat']}")
    for chave, rotulo in (("nsu_antes", "NSU inicial"), ("nsu_solicitado", "NSU solicitado"),
                          ("nsu_sefaz", "NSU informado pela SEFAZ"), ("nsu_salvo", "NSU salvo")):
        if chave in dados:
            partes.append(f"{rotulo}: {dados[chave]}")
    for chave, rotulo in (("lotes_gravados", "Lotes gravados"),
                          ("recuperados_na_tentativa", "Documentos recuperados nesta tentativa"),
                          ("respostas_pendentes", "Respostas aguardando processamento")):
        if chave in dados:
            partes.append(f"{rotulo}: {dados[chave]}")
    if "duracao_segundos" in dados:
        partes.append(f"Duração: {dados['duracao_segundos']:g} s")
    proxima = ""
    if dados.get("proxima_tentativa"):
        instante = datetime.fromisoformat(dados["proxima_tentativa"])
        proxima = f"{instante:%d/%m/%Y %H:%M} (Brasília)"
    return " · ".join(partes), proxima, dados.get("execucao", ""), dados.get("quando")


def _tail_lines(path: Path, max_bytes: int = 6 * 1024 * 1024) -> list[str]:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - max_bytes))
        data = stream.read()
    if size > max_bytes:
        data = data.partition(b"\n")[2]
    return data.decode("utf-8", errors="replace").splitlines()


def read_sync_history(path: Path, limit: int = 10, *, now: datetime | None = None) -> tuple[SyncHistoryItem, ...]:
    """Lê apenas ações SEFAZ; nunca expõe usuário, IP ou mensagem bruta do log."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=SYNC_RETENTION_DAYS)
    items: list[SyncHistoryItem] = []
    for candidate in (path, *(path.with_name(f"{path.name}.{i}") for i in range(1, 6))):
        if not candidate.is_file():
            continue
        for line in reversed(_tail_lines(candidate)):
            try:
                timestamp = line[:19]
                datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                json_start = line.index("{", 19)
                payload = json.loads(line[json_start:])
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("action") not in {"sefaz_sync", "sefaz_sync_auto"}:
                continue
            instant = log_instant(line, payload)
            if instant is None or instant < cutoff:
                continue
            outcome = payload.get("result")
            if outcome not in {"ok", "erro", "ignorado"}:
                continue
            reason = payload.get("reason") if isinstance(payload.get("reason"), str) else None
            detail = _REASONS.get(reason, "Falha na sincronização") if outcome == "erro" else ""
            if reason and reason.startswith("schedule_state_"):
                detail = "Falha no controle do agendamento; confira o acesso ao banco antes da próxima janela"
            incomplete = outcome == "ok" and payload.get("completo") is False
            if incomplete:
                detail = "Ainda há lotes a consultar"
            if outcome == "ok" and payload.get("somente_recuperacao") is True:
                detail = "Recuperação de pendências; consulta de novos documentos adiada"
            if outcome == "ignorado":
                detail = _REASONS.get(reason, "Tentativa não executada")
            interrupted = reason in {"fora_janela", "NfeJanelaConsultaErro"} and any(
                type(payload.get(key)) is int and payload[key] > 0
                for key in ("lotes_gravados", "recuperados_na_tentativa")
            )
            if interrupted:
                detail += ". O que já foi gravado foi mantido; a retomada aguarda a próxima abertura"
            if reason in _ACTIONS and outcome == "erro":
                detail += ". " + _ACTIONS[reason]
            diagnostic, next_attempt, reference, quando = _diagnostico(payload)
            # Registros novos têm instante UTC confiável; não inferir o fuso do prefixo legado.
            parsed = None
            for value in (quando, payload.get('logged_at_utc')):
                if isinstance(value, str):
                    try:
                        candidate_time = datetime.fromisoformat(value)
                        if candidate_time.tzinfo is not None:
                            parsed = candidate_time.astimezone(BRASILIA)
                            break
                    except (ValueError, OverflowError):
                        pass
            if parsed is not None:
                timestamp = parsed.strftime('%d/%m/%Y %H:%M:%S') + ' (Brasília)'
            else:
                parsed = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                timestamp = parsed.strftime('%d/%m/%Y %H:%M:%S') + ' (servidor)'
            group_key = group_when = ''
            if payload['action'] == 'sefaz_sync_auto':
                slot = None
                value = payload.get('scheduled_for')
                if isinstance(value, str):
                    try:
                        candidate_slot = datetime.fromisoformat(value)
                        if candidate_slot.tzinfo is not None:
                            slot = candidate_slot.astimezone(BRASILIA)
                    except (ValueError, OverflowError):
                        pass
                if slot is not None:
                    group_key = 'scheduled:' + slot.isoformat()
                    group_when = slot.strftime('%d/%m/%Y %H:%M') + ' (Brasília)'
                else:
                    # Compatibilidade: agrupar por data/hora, somente no mesmo fuso conhecido.
                    zone = 'Brasília' if parsed.tzinfo is not None else 'servidor'
                    group_key = f'{zone}:{parsed:%Y-%m-%dT%H}'
                    group_when = f'{parsed:%d/%m/%Y %H}h ({zone})'
            items.append(SyncHistoryItem(
                when=timestamp, group_key=group_key, group_when=group_when,
                origin="Automática" if payload["action"] == "sefaz_sync_auto" else "Manual",
                result=("Interrompida pelo horário" if interrupted else "Parcial" if incomplete else
                        {"ok": "Execução concluída", "erro": "Falhou", "ignorado": "Não executada"}[outcome]),
                lots=payload.get("lotes") if isinstance(payload.get("lotes"), int) else None,
                new_events=payload.get("eventos_novos") if isinstance(payload.get("eventos_novos"), int) else None,
                detail=detail,
                diagnostic=diagnostic, next_attempt=next_attempt, reference=reference,
                current_documents=payload.get('documentos_atuais') if type(payload.get('documentos_atuais')) is int else None,
                current_events=payload.get('manifestacoes_atuais') if type(payload.get('manifestacoes_atuais')) is int else None,
                recovered_documents=payload.get('documentos_recuperados') if type(payload.get('documentos_recuperados')) is int else None,
                recovered_events=payload.get('manifestacoes_recuperadas') if type(payload.get('manifestacoes_recuperadas')) is int else None,
            ))
    return _group_history(items, limit)


def _group_history(items: list[SyncHistoryItem], limit: int) -> tuple[SyncHistoryItem, ...]:
    """Uma linha por oportunidade automática; tentativas e contagens originais preservadas."""
    groups: dict[str, list[SyncHistoryItem]] = {}
    order: list[str] = []
    for index, item in enumerate(items):
        key = item.group_key or f'manual:{index}'
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(item)
    result = []
    for key in order[:max(0, limit)]:
        attempts = groups[key]
        # O estado da última execução efetiva prevalece sobre esperas posteriores.
        primary = next((item for item in attempts if item.result != 'Não executada'), attempts[0])
        if primary.group_key and len(attempts) > 1:
            primary = replace(primary, when=primary.group_when, attempts=tuple(reversed(attempts)),
                              next_attempt=attempts[0].next_attempt or primary.next_attempt)
        result.append(primary)
    return tuple(result)
