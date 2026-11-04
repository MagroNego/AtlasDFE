"""Navegação real durante exportação, gravação e I/O de auditoria controlados."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import re
import runpy
import sqlite3
from threading import Event

import httpx
from fastapi.testclient import TestClient

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.web import app as webapp
from nfe_consulta.web import xml_store

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))
CHAVE = f'332611{CNPJ_PADRAO}550010000000011000000000'
NOVA_CHAVE = f'332611{CNPJ_PADRAO}550010000009991000000000'


def configured_app(tmp_path):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    web['criar_banco'](cfg.database_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('INSERT INTO estado_distribuicao(cnpj,ult_nsu,max_nsu) VALUES (?,?,?)',
                     (CNPJ_PADRAO, '1'.zfill(15), '1'.zfill(15)))
        insert_note(conn, CHAVE, 1)
    return cfg, webapp.create_app(cfg)


def insert_note(conn, key, nsu):
    conn.execute('''INSERT INTO manifestacoes
        (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
        VALUES (?,?,'210210','Ciência','2026-11-01T10:00:00-03:00',?,?,'teste')''',
        (CNPJ_PADRAO, key, str(nsu), str(nsu)))


def test_navigation_finishes_while_real_export_worker_is_paused(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original = webapp.gravar_eventos_xlsx

    def paused_export(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'Teste não liberou o exportador'
        return original(*args, **kwargs)

    monkeypatch.setattr(webapp, 'gravar_eventos_xlsx', paused_export)
    cfg, app = configured_app(tmp_path)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/consulta/exportar', headers={'X-NFE-Background': '1'})
            assert response.status_code == 202
            assert await asyncio.to_thread(entered.wait, 3)
            try:
                navigation = await asyncio.wait_for(client.get('/consulta?consultar=1'), 3)
                assert navigation.status_code == 200 and CHAVE in navigation.text
                assert not release.is_set()
                assert app.state.export_queue.active == 1
                assert (await client.get('/healthz')).status_code == 200
            finally:
                release.set()
            await asyncio.wait_for(asyncio.gather(*tuple(app.state.export_jobs.tasks)), 5)
            job = app.state.export_jobs.records[response.json()['job_id']]
            assert job['status'] == 'complete'
            download = await client.get('/downloads/' + job['archive_id'])
            assert download.status_code == 200 and download.content.startswith(b'PK')
        await app.state.export_jobs.close()

    asyncio.run(scenario())


def test_navigation_reads_committed_snapshot_while_writer_holds_sync_lock(tmp_path):
    cfg, app = configured_app(tmp_path)
    entered, release = Event(), Event()

    def writer():
        with app.state.sync_lock:
            with sqlite3.connect(cfg.database_path) as conn:
                conn.execute('BEGIN IMMEDIATE')
                insert_note(conn, NOVA_CHAVE, 2)
                entered.set()
                assert release.wait(10), 'Teste não liberou a transação de escrita'

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        assert entered.wait(3)
        try:
            response = client.get('/consulta?consultar=1')
            assert response.status_code == 200
            assert CHAVE in response.text and NOVA_CHAVE not in response.text
            assert not release.is_set()
            assert client.get('/healthz').status_code == 200
        finally:
            release.set()
        future.result(timeout=5)
        # A consulta seguinte enxerga o commit, sem cache de resultado fiscal.
        response = client.get('/consulta?consultar=1&numero=999')
        assert response.status_code == 200 and NOVA_CHAVE in response.text


def test_navigation_finishes_while_real_import_route_holds_sync_lock(tmp_path, monkeypatch):
    cfg, app = configured_app(tmp_path)
    entered, release = Event(), Event()
    original = xml_store.import_batch
    xml_helpers = runpy.run_path(str(Path(__file__).with_name('test_xml_integrado.py')))

    def paused_import(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'Teste não liberou a importação'
        return original(*args, **kwargs)

    monkeypatch.setattr(xml_store, 'import_batch', paused_import)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        token = re.search(r'name="csrf" value="([^"]+)"', client.get('/xml/importar').text).group(1)
        request = executor.submit(client.post, '/xml/importar', data={'csrf': token},
                                  files={'files': ('nota.xml', xml_helpers['xml'](), 'application/xml')},
                                  follow_redirects=False)
        assert entered.wait(3)
        try:
            assert app.state.sync_lock.locked()
            response = client.get('/consulta?consultar=1')
            assert response.status_code == 200 and CHAVE in response.text
            assert not release.is_set()
            assert client.get('/healthz').status_code == 200
        finally:
            release.set()
        assert request.result(timeout=5).status_code == 303
        assert not app.state.sync_lock.locked()
        with sqlite3.connect(cfg.database_path) as conn:
            assert conn.execute('SELECT chave FROM xml_documentos').fetchone()[0] == xml_helpers['CHAVE']


def test_slow_audit_disk_does_not_block_unrelated_navigation(tmp_path):
    cfg, app = configured_app(tmp_path)
    entered, release, health_finished = Event(), Event(), Event()

    class PausedAuditStream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            if '"action":"consulta_eventos"' in data:
                entered.set()
                assert release.wait(10), 'Teste não liberou a gravação de auditoria'
            return self.stream.write(data)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        handler = app.state.audit._logger.handlers[0]
        original_stream = handler.stream
        handler.stream = PausedAuditStream(original_stream)
        def health():
            try:
                return client.get('/healthz')
            finally:
                health_finished.set()

        consulta = health_request = None
        try:
            consulta = executor.submit(client.get, '/consulta?consultar=1')
            assert entered.wait(3)
            health_request = executor.submit(health)
            # O prazo é só um watchdog. A prova é o health terminar enquanto a
            # escrita real do log e a resposta da consulta continuam suspensas.
            assert health_finished.wait(3), 'I/O do log bloqueou o loop HTTP'
            assert health_request.result().status_code == 200
            assert not consulta.done() and not release.is_set()
        finally:
            release.set()
            try:
                if consulta is not None:
                    consulta.result(timeout=5)
            finally:
                try:
                    if health_request is not None:
                        health_request.result(timeout=5)
                finally:
                    handler.stream = original_stream
        assert CHAVE in consulta.result().text
