"""Arquivo temporário de relatórios: cópias persistentes por 24 horas."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

RETENTION_SECONDS = 24 * 60 * 60
BRASILIA = timezone(timedelta(hours=-3))


def _is_xml(filename, media_type):
    return (filename.lower().endswith('.xml')
            or media_type.split(';', 1)[0].strip().lower() in {'application/xml', 'text/xml'})


def _period_bounds(dates=(), *, start=None, end=None):
    parsed = []
    for value in dates:
        text = str(value or '')[:10]
        for pattern in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                parsed.append(datetime.strptime(text, pattern).date())
                break
            except ValueError:
                continue
    first = start or (min(parsed) if parsed else None)
    last = end or (max(parsed) if parsed else None)
    return first, last


def report_filename(kind, dates=(), *, start=None, end=None, extension='xlsx') -> str:
    first, last = _period_bounds(dates, start=start, end=end)
    period = (f'{first.isoformat() if first else "inicio"}-{last.isoformat() if last else "fim"}'
              if first or last else 'sem_periodo')
    return f'{kind}_{period}.{extension}'


def period_label(dates=(), *, start=None, end=None) -> str:
    first, last = _period_bounds(dates, start=start, end=end)
    if first and last:
        return first.strftime('%d/%m/%Y') if first == last else f'{first:%d/%m/%Y} a {last:%d/%m/%Y}'
    if first:
        return f'A partir de {first:%d/%m/%Y}'
    if last:
        return f'Até {last:%d/%m/%Y}'
    return 'Não informado'


class DownloadArchive:
    def __init__(self, directory: Path):
        self.directory = directory

    def _read(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            return None
        try:
            item = json.loads((self.directory / f'{identifier}.json').read_text(encoding='utf-8'))
            if not isinstance(item, dict) or item.get('id') != identifier:
                return None
            if any(not isinstance(item.get(key), (int, float)) for key in ('expires_at', 'created_at', 'size', 'documents')) or any(not isinstance(item.get(key), str) for key in ('filename', 'media_type', 'period')):
                return None
            return item
        except (OSError, ValueError):
            return None

    def cleanup(self):
        if not self.directory.exists():
            return
        now = time.time()
        for metadata in self.directory.glob('*.json'):
            item = self._read(metadata.stem)
            if item and item['expires_at'] <= now:
                for path in (self.directory / f'{metadata.stem}.bin', metadata):
                    path.unlink(missing_ok=True)
        # Remove arquivos incompletos de execuções interrompidas.
        for path in self.directory.iterdir():
            if path.suffix in {'.bin', '.tmp'}:
                try:
                    if path.stat().st_mtime <= now - RETENTION_SECONDS:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass

    def save(self, source: Path | bytes, *, filename: str, media_type: str,
             documents: int, period: str, admin_only=False):
        if _is_xml(filename, media_type):
            raise ValueError('XMLs originais não são arquivados pelo aplicativo.')
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            self.directory.chmod(0o700)
        self.cleanup()
        identifier = uuid4().hex
        payload = self.directory / f'{identifier}.bin'
        pending = self.directory / f'{identifier}.tmp'
        metadata = self.directory / f'{identifier}.json'
        now = time.time()
        try:
            with os.fdopen(os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as target:
                if isinstance(source, bytes):
                    target.write(source)
                else:
                    with source.open('rb') as original:
                        shutil.copyfileobj(original, target, length=64 * 1024)
            item = dict(id=identifier, filename=Path(filename.replace('\\', '/')).name,
                media_type=media_type, size=payload.stat().st_size, documents=documents,
                period=period, created_at=now, expires_at=now + RETENTION_SECONDS,
                admin_only=bool(admin_only))
            with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as target:
                json.dump(item, target, ensure_ascii=False)
            pending.replace(metadata)
            return item
        except Exception:
            payload.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
            raise

    def discard(self, identifier):
        """Descarta somente o resultado recém-criado de uma tarefa cancelada."""
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Identificador inválido.')
        for suffix in ('.json', '.bin'):
            (self.directory / f'{identifier}{suffix}').unlink(missing_ok=True)

    def list(self, *, admin=False):
        self.cleanup()
        items = []
        for path in self.directory.glob('*.json'):
            item = self.get(path.stem, admin=admin)
            if item:
                item['created_label'] = datetime.fromtimestamp(item['created_at'], BRASILIA).strftime('%d/%m/%Y %H:%M')
                item['expires_label'] = datetime.fromtimestamp(item['expires_at'], BRASILIA).strftime('%d/%m/%Y %H:%M')
                size = item['size']
                item['size_label'] = f'{size / 1024 / 1024:.1f} MB' if size >= 1024 * 1024 else f'{max(1, size / 1024):.0f} KB'
                items.append(item)
        return sorted(items, key=lambda item: item['created_at'], reverse=True)

    def get(self, identifier, *, admin=False):
        item = self._read(identifier)
        if not item or item['expires_at'] <= time.time() or (item.get('admin_only') and not admin):
            return None
        if _is_xml(item['filename'], item['media_type']):
            return None
        if not (self.directory / f'{identifier}.bin').is_file():
            return None
        return item


async def archive_file(request, source, *, filename, media_type, documents, period, admin_only=False):
    from nfe_consulta.web.export_jobs import current_export_job
    job = current_export_job.get()
    if job is not None and job.get("cancel_requested"):
        raise HTTPException(409, "Geração cancelada.")
    try:
        item = await run_in_threadpool(request.app.state.downloads.save, source, filename=filename,
            media_type=media_type, documents=documents, period=period, admin_only=admin_only)
        if job is not None and job.get("cancel_requested"):
            await run_in_threadpool(request.app.state.downloads.discard, item["id"])
            raise HTTPException(409, "Geração cancelada.")
        if job is not None:
            job.update(archive_id=item['id'], documents=documents, period=period)
        return item
    except OSError as exc:
        raise HTTPException(503, 'Não foi possível arquivar o download. Confira espaço e permissões no servidor.') from exc


async def cleanup_loop(archive):
    while True:
        try:
            await run_in_threadpool(archive.cleanup)
        except OSError:
            # Novas tentativas e as rotas nunca liberam arquivos já expirados.
            pass
        await asyncio.sleep(60)
