"""Agendador interno da sincronização automática da SEFAZ."""

from __future__ import annotations

from nfe_consulta.janela_consulta import BRASILIA, NfeJanelaConsultaErro, proxima_abertura

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.modelos import NfeConsumoIndevidoErro, NfeLimiteConsultaErro
from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web.sync_diagnostics import detalhes_erro
from nfe_consulta.web.sync_runtime import (
    caminho_banco_configurado,
    sincronizar_configurado,
)


def proxima_execucao(
    agora: datetime,
    *,
    hora: int,
    minuto: int,
    dias_semana: Iterable[int],
) -> datetime:
    """Calcula a próxima execução no fuso horário do servidor.

    Segunda-feira = 0 e domingo = 6.
    """
    dias = frozenset(dias_semana)
    if not dias:
        raise ValueError("Ao menos um dia da semana deve ser configurado.")
    if any(dia < 0 or dia > 6 for dia in dias):
        raise ValueError("Dias da semana devem estar entre 0 e 6.")
    if not 0 <= hora <= 23:
        raise ValueError("Hora inválida.")
    if not 0 <= minuto <= 59:
        raise ValueError("Minuto inválido.")

    for deslocamento in range(8):
        dia = agora.date() + timedelta(days=deslocamento)
        if dia.weekday() not in dias:
            continue
        candidato = datetime.combine(
            dia,
            datetime.min.time(),
            tzinfo=agora.tzinfo,
        ).replace(hour=hora, minute=minuto)
        if candidato > agora:
            return candidato

    raise RuntimeError("Não foi possível calcular a próxima sincronização.")


def horario_unico(dia: date, hora: int, minuto: int) -> datetime:
    """Horário civil de Brasília para o teste de data única."""
    return datetime.combine(
        dia, time(hora, minuto),
        tzinfo=timezone(timedelta(hours=-3)),
    )


def agora_brasilia() -> datetime:
    return datetime.now(timezone(timedelta(hours=-3)))


def deve_recuperar_execucao(
    agora: datetime,
    ultima_execucao_local: date | None,
    *,
    hora: int,
    minuto: int,
    dias_semana: Iterable[int],
) -> bool:
    """Indica se o serviço subiu depois do horário e precisa recuperar o dia."""
    dias = frozenset(dias_semana)
    if agora.weekday() not in dias:
        return False

    horario = agora.replace(
        hour=hora,
        minute=minuto,
        second=0,
        microsecond=0,
    )
    if agora < horario:
        return False

    return ultima_execucao_local != agora.date()


def _garantir_tabela_agendamento(conexao) -> None:
    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS controle_agendamento (
            cnpj TEXT PRIMARY KEY,
            ultima_execucao_local TEXT NOT NULL,
            atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conexao.commit()


def ultima_execucao_agendada(settings) -> date | None:
    """Lê do banco a última data local em que a sincronização terminou."""
    banco = caminho_banco_configurado(settings)
    if not banco.is_file():
        return None

    conexao = abrir_banco(
        banco,
        settings.current_database_password(),
    )
    try:
        _garantir_tabela_agendamento(conexao)
        linha = conexao.execute(
            "SELECT ultima_execucao_local FROM controle_agendamento "
            "WHERE cnpj = ?",
            (CNPJ_PADRAO,),
        ).fetchone()
    finally:
        conexao.close()

    if not linha:
        return None
    return date.fromisoformat(linha[0])


def registrar_execucao_agendada(settings, dia_local: date) -> None:
    """Marca uma sincronização concluída para evitar repetição em reinícios."""
    banco = caminho_banco_configurado(settings)
    if not banco.is_file():
        raise FileNotFoundError(f"Banco configurado não encontrado: {banco}")

    conexao = abrir_banco(
        banco,
        settings.current_database_password(),
    )
    try:
        _garantir_tabela_agendamento(conexao)
        with conexao:
            conexao.execute(
                """
                INSERT INTO controle_agendamento(cnpj, ultima_execucao_local)
                VALUES (?, ?)
                ON CONFLICT(cnpj) DO UPDATE SET
                    ultima_execucao_local = excluded.ultima_execucao_local,
                    atualizado_em = CURRENT_TIMESTAMP
                """,
                (CNPJ_PADRAO, dia_local.isoformat()),
            )
    finally:
        conexao.close()


async def _executar_sincronizacao_automatica(
    app,
    *,
    dia_agendado: date | None = None,
    janela_agendada: datetime | None = None,
) -> datetime | None:
    """Executa uma sincronização automática respeitando lock, cooldown e persistência."""
    settings = getattr(app.state, "scheduler_settings", app.state.settings)
    if not getattr(settings, "auto_sync_enabled", True):
        return None
    dia = dia_agendado or datetime.now().astimezone().date()

    def audit_result(result, **details):
        details.setdefault('quando', datetime.now(BRASILIA).isoformat())
        if janela_agendada is not None and janela_agendada.tzinfo is not None:
            details['scheduled_for'] = janela_agendada.astimezone(BRASILIA).isoformat()
        app.state.audit.write_system('sefaz_sync_auto', result, **details)

    lock = app.state.sync_lock
    if not lock.acquire(blocking=False):
        audit_result(
            "ignorado",
            reason="sync_in_progress",
        )
        return

    try:
        try:
            resumo = await asyncio.to_thread(
                sincronizar_configurado,
                settings,
                max_lotes=settings.auto_sync_max_lotes,
                audit=app.state.audit,
            )
        except NfeJanelaConsultaErro as exc:
            audit_result("ignorado", reason="fora_janela", **detalhes_erro(exc))
            return proxima_abertura(agora_brasilia())
        except NfeLimiteConsultaErro as exc:
            audit_result(
                "ignorado",
                reason="cooldown",
                **detalhes_erro(exc),
            )
            return await asyncio.to_thread(proxima_tentativa_permitida, settings)
        except NfeConsumoIndevidoErro as exc:
            audit_result(
                "erro",
                reason="consumo_indevido_656",
                **detalhes_erro(exc),
            )
            return await asyncio.to_thread(proxima_tentativa_permitida, settings)
        except Exception as exc:
            audit_result(
                "erro",
                reason=type(exc).__name__,
                **detalhes_erro(exc),
            )
            return proximo_disparo_configurado(app)

        try:
            if resumo.completo:
                if janela_agendada is not None:
                    await asyncio.to_thread(registrar_janela_concluida, settings, janela_agendada)
                else:
                    await asyncio.to_thread(registrar_execucao_agendada, settings, dia)
        except Exception as exc:
            audit_result("erro",
                reason=f"schedule_state_{type(exc).__name__}",
            )
            return proximo_disparo_configurado(app)

        audit_result(
            "ok",
            lotes=resumo.lotes,
            eventos_novos=resumo.eventos_novos,
            documentos_atuais=getattr(resumo, "documentos_atuais", None),
            manifestacoes_atuais=getattr(resumo, "manifestacoes_atuais", None),
            documentos_recuperados=getattr(resumo, "documentos_recuperados", None),
            manifestacoes_recuperadas=getattr(resumo, "manifestacoes_recuperadas", None),
            ult_nsu=resumo.ult_nsu,
            max_nsu=resumo.max_nsu,
            somente_recuperacao=getattr(resumo, "somente_recuperacao", False),
            completo=resumo.completo,
            cache=resumo.cache,
            cooldown_minutos=settings.sync_cooldown_minutes,
            recuperacao_cons_nsu=getattr(settings, "recovery_enabled", True),
        )
    finally:
        lock.release()


async def executar_sincronizacao_unica(app, dia: date) -> None:
    """Registra a tentativa antes da chamada para não repetir após reinício."""
    settings = getattr(app.state, "scheduler_settings", app.state.settings)
    try:
        if await asyncio.to_thread(ultima_execucao_agendada, settings) == dia:
            return
        await asyncio.to_thread(registrar_execucao_agendada, settings, dia)
    except Exception as exc:
        app.state.audit.write_system(
            "sefaz_sync_auto", "erro",
            reason=f"schedule_state_{type(exc).__name__}",
        )
        return
    await executar_sincronizacao_automatica(app, dia_agendado=dia)


async def _loop_sincronizacao_automatica(app) -> None:
    """Executa uma data única ou mantém a rotina diária opcional."""
    settings = getattr(app.state, "scheduler_settings", app.state.settings)
    if settings.auto_sync_once_date is not None:
        # A execução de data única usa o horário de Brasília.
        alvo = horario_unico(
            settings.auto_sync_once_date,
            settings.auto_sync_hour,
            settings.auto_sync_minute,
        )
        app.state.auto_sync_next_at = alvo
        agora = agora_brasilia()
        if agora < alvo:
            await asyncio.sleep((alvo - agora).total_seconds())
        try:
            if agora_brasilia().date() == settings.auto_sync_once_date:
                await executar_sincronizacao_unica(app, settings.auto_sync_once_date)
        finally:
            app.state.auto_sync_next_at = None
        return

    while True:
        settings = getattr(app.state, "scheduler_settings", app.state.settings)
        if not getattr(settings, "auto_sync_enabled", True):
            app.state.auto_sync_next_at = None
            await _wait_schedule(app, 60.0)
            continue
        agora = agora_brasilia()
        janela, proxima = janelas_diarias(agora, times=getattr(settings, "auto_sync_times", None), weekdays=getattr(settings, "auto_sync_weekdays", tuple(range(7))))
        abertura = proxima_abertura(agora)
        if abertura > agora:
            app.state.auto_sync_next_at = proxima
            await _wait_schedule(app, min(60.0, (proxima - agora).total_seconds()))
            continue
        recuperacao = await asyncio.to_thread(proxima_recuperacao_nsu, settings)
        adiada = None
        app.state.auto_sync_next_at = proxima
        from nfe_consulta.janela_consulta import inicio_janela
        inicio = inicio_janela(agora)
        elegivel = janela >= inicio and agora.weekday() in settings.auto_sync_weekdays
        if elegivel:
            try:
                ultima = await asyncio.to_thread(ultima_janela_concluida, settings)
            except Exception as exc:
                # Sem o estado persistido, não arrisque repetir uma execução.
                app.state.audit.write_system(
                    "sefaz_scheduler", "erro",
                    reason=f"schedule_state_{type(exc).__name__}",
                )
                adiada = proxima_abertura(proxima)
            else:
                if (ultima is None or ultima < janela
                        or (recuperacao is not None and recuperacao <= agora)):
                    if settings is not getattr(app.state, "scheduler_settings", app.state.settings):
                        continue
                    adiada = await executar_sincronizacao_automatica(app, janela_agendada=janela)
        agora = agora_brasilia()
        # Rejeições com cursor recuperável retomam após a pausa, sem esperar
        # pelo próximo disparo horário e sem repetir durante o bloqueio.
        recuperacao = await asyncio.to_thread(proxima_recuperacao_nsu, settings)
        if elegivel and recuperacao is not None:
            proxima = min(proxima, max(recuperacao, agora + timedelta(seconds=60)))
        if adiada is not None:
            # Uma tentativa bloqueada/erro de configuração também limita a
            # recuperação pendente; não repita a falha a cada minuto.
            proxima = max(adiada, agora + timedelta(seconds=1))
        proxima = proxima_abertura(proxima)
        app.state.auto_sync_next_at = proxima
        espera = max(1.0, (proxima - agora).total_seconds())
        # Uma tentativa manual pode registrar recuperação enquanto o agendador
        # aguarda a janela diária. Releia o banco sem consultar a SEFAZ.
        if recuperacao is None and adiada is None:
            espera = min(espera, 60.0)
        await _wait_schedule(app, espera)


def proxima_tentativa_permitida(settings) -> datetime | None:
    """Reagenda uma tentativa bloqueada, inclusive 656 sem NSU recuperável."""
    from nfe_consulta.web.status_view import read_web_status

    try:
        status = read_web_status(
            caminho_banco_configurado(settings), CNPJ_PADRAO,
            password=settings.current_database_password(),
        )
    except Exception:
        return None
    return status.available_at


def proxima_recuperacao_nsu(settings) -> datetime | None:
    if not getattr(settings, "recovery_enabled", True):
        return None
    from nfe_consulta.web.status_view import read_web_status

    try:
        status = read_web_status(
            caminho_banco_configurado(settings), CNPJ_PADRAO,
            password=settings.current_database_password(),
        )
    except Exception:
        # A rotina principal já registra falhas de banco/configuração.
        return None
    if status.recovery_pending or any(retomado is None for _, _, retomado in status.nsu_gaps):
        return status.available_at or agora_brasilia()
    return None


async def _wait_schedule(app, seconds):
    wake = getattr(app.state, "auto_sync_wake", None)
    if wake is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(wake.wait(), timeout=seconds)
    except TimeoutError:
        pass
    finally:
        wake.clear()


def janelas_diarias(agora: datetime, *, times=None, weekdays=tuple(range(7))) -> tuple[datetime, datetime]:
    """Último disparo e próximo disparo nos dias civis de Brasília selecionados."""
    from nfe_consulta.web.auto_sync_config import DEFAULT_TIMES, validate_schedule
    from nfe_consulta.janela_consulta import janela_atual
    window = janela_atual()
    values = validate_schedule(True, times or DEFAULT_TIMES, weekdays,
        window_start=window.inicio, window_end=window.fim)
    if agora.tzinfo is None:
        raise ValueError("O instante deve ter fuso horário.")
    agora = agora.astimezone(timezone(timedelta(hours=-3)))
    candidatos = sorted(
        datetime.combine(agora.date() + timedelta(days=day), time.fromisoformat(value), tzinfo=agora.tzinfo)
        for day in range(-8, 9)
        if (agora.date() + timedelta(days=day)).weekday() in values['auto_sync_weekdays']
        for value in values['auto_sync_times']
    )
    return max(value for value in candidatos if value <= agora), min(value for value in candidatos if value > agora)


def janelas_intervalo(
    agora: datetime, *, intervalo_horas: int = 8, hora: int = 8, minuto: int = 0,
) -> tuple[datetime, datetime]:
    """Janela atual e próxima no horário de Brasília, sem depender do reinício."""
    if intervalo_horas not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("O intervalo deve dividir 24 horas.")
    if not 0 <= hora <= 23 or not 0 <= minuto <= 59:
        raise ValueError("Horário inválido.")
    if agora.tzinfo is None:
        raise ValueError("O instante deve ter fuso horário.")
    agora = agora.astimezone(timezone(timedelta(hours=-3)))
    ancora = datetime(2000, 1, 1, hora, minuto, tzinfo=agora.tzinfo)
    intervalo = timedelta(hours=intervalo_horas)
    janela = ancora + ((agora - ancora) // intervalo) * intervalo
    return janela, janela + intervalo


def _garantir_tabela_intervalo(conexao) -> None:
    conexao.execute("""CREATE TABLE IF NOT EXISTS controle_agendamento_intervalo (
        cnpj TEXT PRIMARY KEY, ultima_janela TEXT NOT NULL,
        atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conexao.commit()


def ultima_janela_concluida(settings) -> datetime | None:
    banco = caminho_banco_configurado(settings)
    if not banco.is_file():
        raise FileNotFoundError("Banco da sincronização não encontrado.")
    conexao = abrir_banco(banco, settings.current_database_password())
    try:
        _garantir_tabela_intervalo(conexao)
        linha = conexao.execute(
            "SELECT ultima_janela FROM controle_agendamento_intervalo WHERE cnpj=?",
            (CNPJ_PADRAO,),
        ).fetchone()
        return datetime.fromisoformat(linha[0]) if linha else None
    finally:
        conexao.close()


def registrar_janela_concluida(settings, janela: datetime) -> None:
    banco = caminho_banco_configurado(settings)
    if not banco.is_file():
        raise FileNotFoundError("Banco da sincronização não encontrado.")
    conexao = abrir_banco(banco, settings.current_database_password())
    try:
        _garantir_tabela_intervalo(conexao)
        with conexao:
            conexao.execute("""INSERT INTO controle_agendamento_intervalo(cnpj, ultima_janela)
                VALUES (?, ?) ON CONFLICT(cnpj) DO UPDATE SET
                ultima_janela=excluded.ultima_janela, atualizado_em=CURRENT_TIMESTAMP""",
                (CNPJ_PADRAO, janela.isoformat()),
            )
    finally:
        conexao.close()


async def executar_sincronizacao_automatica(app, *, dia_agendado=None, janela_agendada=None):
    from nfe_consulta.janela_consulta import usar_janela
    with usar_janela(lambda: getattr(app.state, 'scheduler_settings', app.state.settings)):
        return await _executar_sincronizacao_automatica(app, dia_agendado=dia_agendado, janela_agendada=janela_agendada)


async def loop_sincronizacao_automatica(app):
    from nfe_consulta.janela_consulta import usar_janela
    with usar_janela(lambda: getattr(app.state, 'scheduler_settings', app.state.settings)):
        return await _loop_sincronizacao_automatica(app)


def proximo_disparo_configurado(app):
    """Calcula a retentativa com horários e janela da mesma revisão da configuração."""
    from nfe_consulta.janela_consulta import usar_janela
    current = getattr(app.state, 'scheduler_settings', app.state.settings)
    with usar_janela(lambda: current):
        _, next_run = janelas_diarias(agora_brasilia(),
            times=getattr(current, 'auto_sync_times', None),
            weekdays=getattr(current, 'auto_sync_weekdays', tuple(range(7))))
        return proxima_abertura(next_run)
