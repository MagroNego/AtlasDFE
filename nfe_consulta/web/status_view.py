"""Estado estruturado do banco para a interface web."""

from __future__ import annotations

from nfe_consulta import janela_consulta
from nfe_consulta.janela_consulta import BRASILIA, proxima_abertura

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nfe_consulta.seguranca_banco import abrir_banco


@dataclass(frozen=True)
class WebStatus:
    database_ready: bool
    updated_at: datetime | None
    ult_nsu: str | None
    max_nsu: str | None
    last_attempt: datetime | None
    next_attempt: datetime | None
    pause_until: datetime | None
    pause_reason: str | None
    nsu_gaps: tuple = ()
    pending_responses: int = 0
    recovery_pending: bool = False
    recovery_received: int = 0
    recovery_unavailable: int = 0
    recovery_intervals: tuple = ()

    @property
    def complete(self) -> bool:
        return bool(self.ult_nsu and self.max_nsu and self.ult_nsu == self.max_nsu
                    and not self.nsu_gaps and not self.pending_responses)

    @property
    def cooldown_active(self) -> bool:
        return bool(self.next_attempt and self.next_attempt > datetime.now(timezone.utc))

    @property
    def pause_active(self) -> bool:
        return bool(self.pause_until and self.pause_until > datetime.now(timezone.utc))

    @property
    def available_at(self) -> datetime | None:
        candidatos = [
            value for value in (self.next_attempt, self.pause_until)
            if value is not None
        ]
        agora = janela_consulta.agora_brasilia()
        alvo = max([agora, *candidatos])
        abertura = proxima_abertura(alvo)
        return abertura if abertura > agora else (max(candidatos) if candidatos else None)

    @property
    def can_sync(self) -> bool:
        agora = janela_consulta.agora_brasilia()
        return self.database_ready and (
            self.available_at is None or self.available_at <= agora
        )

    def local_time(self, value: datetime | None) -> str:
        if value is None:
            return "Sem registro"
        return value.astimezone(BRASILIA).strftime("%d/%m/%Y %H:%M")

    @property
    def updated_label(self) -> str:
        return self.local_time(self.updated_at)

    @property
    def next_attempt_label(self) -> str:
        return self.local_time(self.next_attempt)

    @property
    def pause_until_label(self) -> str:
        return self.local_time(self.pause_until)

    @property
    def available_label(self) -> str:
        return self.local_time(self.available_at)


def _utc_sqlite(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_web_status(
    database_path: Path,
    cnpj: str,
    *,
    password: str | None = None,
) -> WebStatus:
    caminho = Path(database_path).expanduser()
    if not caminho.is_file():
        return WebStatus(False, None, None, None, None, None, None, None)

    conexao = abrir_banco(caminho, password, somente_leitura=True)
    try:
        try:
            estado = conexao.execute(
                "SELECT ult_nsu, max_nsu, atualizado_em "
                "FROM estado_distribuicao WHERE cnpj = ?",
                (cnpj,),
            ).fetchone()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            estado = None

        try:
            controle = conexao.execute(
                "SELECT ultima_tentativa_em, proxima_tentativa_em "
                "FROM controle_sincronizacao WHERE cnpj = ?",
                (cnpj,),
            ).fetchone()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            controle = None

        try:
            pausa = conexao.execute(
                "SELECT ate_utc, motivo FROM pausa_distribuicao WHERE cnpj = ?",
                (cnpj,),
            ).fetchone()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            pausa = None
        try:
            gaps = conexao.execute(
                "SELECT nsu_local, nsu_sefaz, retomado_em FROM recuperacoes_nsu "
                "WHERE cnpj=? ORDER BY id DESC", (cnpj,),
            ).fetchall()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            gaps = ()
        recovery_pending = False
        recovery_received = recovery_unavailable = 0
        recovery_intervals = []
        try:
            recovery_received = conexao.execute("SELECT COUNT(*) FROM consultas_nsu WHERE cnpj=? AND resultado='recebido'", (cnpj,)).fetchone()[0]
            recovery_unavailable = conexao.execute("SELECT COUNT(*) FROM consultas_nsu WHERE cnpj=? AND resultado='indisponivel'", (cnpj,)).fetchone()[0]
            from nfe_consulta.banco import BancoManifestacoes
            leitor = object.__new__(BancoManifestacoes)
            leitor.conexao = conexao
            recovery_pending = leitor.proximo_nsu_faltante(cnpj) is not None
            incompletos = []
            for local, remoto, retomado in gaps:
                recebidos = conexao.execute("SELECT COUNT(*) FROM documentos_nsu WHERE cnpj=? AND nsu>? AND nsu<=?", (cnpj,local,remoto)).fetchone()[0]
                total = int(remoto)-int(local)
                recovery_intervals.append((str(int(local)+1).zfill(15), remoto, recebidos, total))
                if recebidos < total:
                    incompletos.append((local, remoto, retomado))
            gaps = incompletos
            recovery_pending = recovery_pending or bool(conexao.execute(
                "SELECT 1 FROM consultas_nsu WHERE cnpj=? AND resultado='pendente' LIMIT 1", (cnpj,)).fetchone())
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            # Antes da primeira abertura para escrita, a migração aditiva
            # ainda não criou as tabelas. As lacunas continuam na fila.
            recovery_pending = bool(gaps)
            recovery_intervals = [(str(int(g[0])+1).zfill(15),g[1],0,int(g[1])-int(g[0])) for g in gaps]
        try:
            pending = conexao.execute(
                "SELECT COUNT(*) FROM respostas_distribuicao WHERE cnpj=? AND processado_em IS NULL",
                (cnpj,),
            ).fetchone()[0]
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            pending = 0
    finally:
        conexao.close()

    return WebStatus(
        database_ready=True,
        updated_at=_utc_sqlite(estado[2]) if estado else None,
        ult_nsu=estado[0] if estado else None,
        max_nsu=estado[1] if estado else None,
        last_attempt=_iso(controle[0]) if controle else None,
        next_attempt=_iso(controle[1]) if controle else None,
        pause_until=_iso(pausa[0]) if pausa else None,
        pause_reason=pausa[1] if pausa else None,
        nsu_gaps=tuple(gaps),
        pending_responses=pending,
        recovery_pending=recovery_pending,
        recovery_received=recovery_received,
        recovery_unavailable=recovery_unavailable,
        recovery_intervals=tuple(recovery_intervals),
    )
