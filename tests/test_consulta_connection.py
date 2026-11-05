"""A prontidão e os dados da Consulta compartilham uma conexão por requisição."""
from dataclasses import replace
from pathlib import Path
import runpy
import sqlite3
import sys
from threading import get_ident

from fastapi.testclient import TestClient
import pytest

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta import seguranca_banco
from nfe_consulta.web import app as webapp

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))
KEY = f'332611{CNPJ_PADRAO}550010000000011000000000'


def ready_settings(tmp_path, encrypted=False):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    web['criar_banco'](cfg.database_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('INSERT INTO estado_distribuicao(cnpj,ult_nsu,max_nsu) VALUES (?,?,?)',
                     (CNPJ_PADRAO, '1'.zfill(15), '1'.zfill(15)))
        conn.execute('''INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,'210210','Ciência','2026-11-01','1','1','teste')''', (CNPJ_PADRAO, KEY))
    if encrypted:
        target = tmp_path / 'dados' / 'protegido.db'
        password = 'Senha sintética para medir conexão'
        seguranca_banco.migrar_banco(cfg.database_path, target, password)
        cfg = replace(cfg, database_path=target, database_password=password)
    return cfg


def observe_connections(monkeypatch):
    original = seguranca_banco.abrir_banco
    opened, closed, operations = [], [], []

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, *args, **kwargs):
            operations.append((id(self), get_ident()))
            return self.conn.execute(*args, **kwargs)

        def close(self):
            closed.append((id(self), get_ident()))
            return self.conn.close()

    def connect(*args, **kwargs):
        conn = Connection(original(*args, **kwargs))
        opened.append((id(conn), get_ident(), kwargs.get('somente_leitura')))
        return conn

    for name in ('nfe_consulta.seguranca_banco', 'nfe_consulta.web.consulta_local',
                 'nfe_consulta.web.consulta_data'):
        if name in sys.modules:
            monkeypatch.setattr(sys.modules[name], 'abrir_banco', connect)
    return opened, closed, operations


@pytest.mark.parametrize('encrypted', [False, True])
def test_consulta_readiness_and_rows_open_database_only_once(tmp_path, monkeypatch, encrypted):
    cfg = ready_settings(tmp_path, encrypted)
    opened, closed, operations = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200 and KEY in response.text
    assert len(opened) == 1
    assert opened[0][2] is True
    assert closed == [opened[0][:2]]
    assert all(operation == opened[0][:2] for operation in operations)


def test_missing_database_is_not_created(tmp_path, monkeypatch):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200
    assert 'Banco indisponível' in response.text
    assert not cfg.database_path.exists()
    assert opened == closed == []


def test_legacy_database_without_ready_state_skips_query_and_closes(tmp_path, monkeypatch):
    from nfe_consulta.web import consulta_data
    cfg = ready_settings(tmp_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('DROP TABLE estado_distribuicao')

    def forbidden(*args, **kwargs):
        raise AssertionError('Banco não sincronizado não deve consultar manifestações')

    monkeypatch.setattr(consulta_data, 'consultar_eventos_na_conexao', forbidden)
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200
    assert 'ainda não possui uma sincronização concluída' in response.text
    assert KEY not in response.text
    assert len(opened) == len(closed) == 1


def test_imported_xml_readiness_fallback_uses_same_connection(tmp_path, monkeypatch):
    cfg = ready_settings(tmp_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('DROP TABLE estado_distribuicao')
        conn.execute('CREATE TABLE xml_documentos(cnpj TEXT,chave TEXT)')
        conn.execute('INSERT INTO xml_documentos VALUES (?,?)', (CNPJ_PADRAO, KEY))
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200 and KEY in response.text
    assert 'Banco local disponível' in response.text
    assert len(opened) == len(closed) == 1


def test_wrong_password_preserves_friendly_unready_notice(tmp_path):
    cfg = replace(ready_settings(tmp_path, True), database_password='Senha incorreta')
    before = cfg.database_path.read_bytes()
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200 and KEY not in response.text
    assert 'ainda não está configurado ou sincronizado' in response.text
    assert 'Senha incorreta ou banco criptografado inválido' not in response.text
    assert cfg.database_path.read_bytes() == before


def test_query_error_closes_connection_and_keeps_ready_label(tmp_path, monkeypatch):
    from nfe_consulta.web import consulta_data
    cfg = ready_settings(tmp_path)

    def fail_query(*args, **kwargs):
        raise sqlite3.OperationalError('falha de consulta sintética')

    monkeypatch.setattr(consulta_data, 'consultar_eventos_na_conexao', fail_query)
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200
    assert 'Banco disponível' in response.text
    assert 'OperationalError: falha de consulta sintética' in response.text
    assert len(opened) == len(closed) == 1


@pytest.mark.parametrize('url', ['/consulta', '/consulta?consultar=1&numero=abc'])
def test_empty_screen_and_invalid_filters_do_not_fetch_rows(tmp_path, monkeypatch, url):
    cfg = ready_settings(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError('Filtros inválidos ou tela inicial não consultam linhas')

    monkeypatch.setattr(webapp, 'consultar_pagina', forbidden)
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get(url)
    assert response.status_code == 200 and KEY not in response.text
    assert len(opened) == len(closed) == 1
    if 'abc' in url:
        assert 'somente números' in response.text


def test_each_request_gets_fresh_connection_and_committed_data(tmp_path, monkeypatch):
    cfg = ready_settings(tmp_path)
    new_key = KEY[:25] + '000000002' + KEY[34:]
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        first = client.get('/consulta?consultar=1')
        assert KEY in first.text and new_key not in first.text
        with sqlite3.connect(cfg.database_path) as conn:
            conn.execute('''INSERT INTO manifestacoes
                (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
                VALUES (?,?,'210210','Ciência','2026-11-02','2','2','teste')''', (CNPJ_PADRAO, new_key))
        second = client.get('/consulta?consultar=1')
        assert KEY in second.text and new_key in second.text
        assert second.headers['cache-control'] == 'no-store'
    assert len(opened) == len(closed) == 2


def test_borrowed_connection_remains_owned_by_caller(tmp_path):
    from nfe_consulta.web.consulta_local import consultar_eventos_na_conexao, FiltrosEventos
    cfg = ready_settings(tmp_path)
    with sqlite3.connect(cfg.database_path) as conn:
        result = consultar_eventos_na_conexao(conn, CNPJ_PADRAO, FiltrosEventos(), agrupar_cartas=True)
        assert result.total == 1
        assert conn.execute('SELECT 1').fetchone() == (1,)


@pytest.mark.parametrize('unreadable', [False, True])
def test_unavailable_password_file_renders_friendly_notice(tmp_path, monkeypatch, unreadable):
    cfg = ready_settings(tmp_path)
    secret = tmp_path / 'secrets' / 'db-password.txt'
    secret.parent.mkdir(parents=True)
    secret.write_text('valid password' if unreadable else '', encoding='utf-8')
    cfg = replace(cfg, database_password_file=secret)
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == secret and unreadable:
            raise OSError('Falha sintética ao ler segredo')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta?consultar=1')
    assert response.status_code == 200
    assert 'ainda não está configurado ou sincronizado' in response.text
    assert KEY not in response.text
    assert 'RuntimeError' not in response.text and 'OSError' not in response.text


def test_password_file_is_resolved_once_per_request_and_not_cached(tmp_path, monkeypatch):
    cfg = ready_settings(tmp_path, True)
    password = cfg.database_password
    secret = tmp_path / 'secrets' / 'db-password.txt'
    secret.parent.mkdir(parents=True)
    secret.write_text(password, encoding='utf-8')
    cfg = replace(cfg, database_password_file=secret, database_password=None)
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path == secret:
            reads.append(get_ident())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    opened, closed, _ = observe_connections(monkeypatch)
    with TestClient(webapp.create_app(cfg)) as client:
        assert KEY in client.get('/consulta?consultar=1').text
        secret.write_text('Senha incorreta', encoding='utf-8')
        assert KEY not in client.get('/consulta?consultar=1').text
        secret.write_text(password, encoding='utf-8')
        assert KEY in client.get('/consulta?consultar=1').text
    assert len(reads) == 3
    assert len(opened) == len(closed) == 2
    assert reads[0] == opened[0][1] and reads[2] == opened[1][1]
