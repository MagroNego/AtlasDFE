"""Uma conexão por pedido da Emissão, incluindo cabeçalho e detalhes."""
from dataclasses import replace
from pathlib import Path
import runpy
import sqlite3
import sys
from threading import get_ident

from fastapi.testclient import TestClient
import pytest

from nfe_consulta import seguranca_banco
from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.web.app import create_app
from nfe_consulta.web import xml_store

h = runpy.run_path(str(Path(__file__).with_name('test_xml_integrado.py')))
KEY = h['CHAVE']


def configured(tmp_path, encrypted=False):
    cfg = replace(h['helpers']['settings_web'](tmp_path), local_auth_enabled=False)
    h['helpers']['criar_banco'](cfg.database_path)
    source = tmp_path / 'nota.xml'
    source.write_bytes(h['xml']())
    xml_store.import_batch(cfg.database_path, CNPJ_PADRAO, [(source, 'nota.xml')])
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('''INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,'210210','Ciência','2026-11-01','1','1','teste')''', (CNPJ_PADRAO, KEY))
        conn.execute('''INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,'110110','Carta','2026-11-02','2','2','teste')''', (CNPJ_PADRAO, KEY))
        conn.execute('''INSERT INTO cartas_correcao(cnpj,chave,protocolo,sequencia,correcao)
            VALUES (?,?,'2','1','Texto da correção para conexão única')''', (CNPJ_PADRAO, KEY))
    if encrypted:
        target = tmp_path / 'dados' / 'protegido.db'
        password = 'Senha sintética da Emissão'
        seguranca_banco.migrar_banco(cfg.database_path, target, password)
        cfg = replace(cfg, database_path=target, database_password=password)
    return cfg


def observe(monkeypatch):
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

    for module in ('nfe_consulta.seguranca_banco', 'nfe_consulta.web.xml_store',
                   'nfe_consulta.web.consulta_local', 'nfe_consulta.web.xml_routes',
                   'nfe_consulta.web.emissao_data'):
        if module in sys.modules and hasattr(sys.modules[module], 'abrir_banco'):
            monkeypatch.setattr(sys.modules[module], 'abrir_banco', connect)
    return opened, closed, operations


@pytest.mark.parametrize('encrypted', [False, True])
@pytest.mark.parametrize('route', ['/xml', '/xml/itens/' + KEY, '/xml/manifestacoes/' + KEY])
def test_emission_request_opens_only_one_connection(tmp_path, monkeypatch, encrypted, route):
    cfg = configured(tmp_path, encrypted)
    opened, closed, operations = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        response = client.get(route)
    assert response.status_code == 200 and KEY in response.text
    assert len(opened) == 1
    assert opened[0][2] is True
    assert closed == [opened[0][:2]]
    assert all(operation == opened[0][:2] for operation in operations)
    if route == '/xml':
        assert 'Texto da correção para conexão única' in response.text


@pytest.mark.parametrize('route,status', [('/xml', 200), ('/xml/itens/' + KEY, 404),
                                         ('/xml/manifestacoes/' + KEY, 404)])
def test_missing_database_does_not_get_created(tmp_path, monkeypatch, route, status):
    cfg = replace(h['helpers']['settings_web'](tmp_path), local_auth_enabled=False)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        assert client.get(route).status_code == status
    assert not cfg.database_path.exists()
    assert opened == closed == []


@pytest.mark.parametrize('route', ['/xml/itens/' + '0' * 44 + '?pagina=0&item=' + 'x' * 201,
                                  '/xml/manifestacoes/' + '0' * 44 + '?pagina=0&data_inicial=invalida'])
def test_missing_note_keeps_priority_over_invalid_detail_filters(tmp_path, monkeypatch, route):
    cfg = configured(tmp_path)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        assert client.get(route).status_code == 404
    assert len(opened) == len(closed) == 1


def test_manifestations_without_events_table_keep_empty_screen(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('DROP TABLE manifestacoes')
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        response = client.get('/xml/manifestacoes/' + KEY + '?pagina=9')
    assert response.status_code == 200 and 'Nenhum evento encontrado' in response.text
    assert 'MANIFESTAÇÕES | NF 123' in response.text
    assert len(opened) == len(closed) == 1


def test_correction_failure_keeps_note_rows_and_closes_connection(tmp_path, monkeypatch):
    from nfe_consulta.web import emissao_data
    cfg = configured(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError('Falha sintética nas cartas')

    monkeypatch.setattr(emissao_data, 'consultar_cartas_na_conexao', fail)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        response = client.get('/xml')
    assert response.status_code == 200 and KEY in response.text
    assert 'Falha sintética nas cartas' in response.text
    assert len(opened) == len(closed) == 1


def test_query_failure_closes_connection_and_returns_friendly_page(tmp_path, monkeypatch):
    cfg = configured(tmp_path)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('Falha sintética na leitura')

    monkeypatch.setattr(xml_store, 'query_report_na_conexao', fail)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        response = client.get('/xml')
    assert response.status_code == 200 and KEY not in response.text
    assert 'Confira a configuração do banco' in response.text
    assert 'Falha sintética' not in response.text
    assert len(opened) == len(closed) == 1


@pytest.mark.parametrize('route,status', [('/xml', 200), ('/xml/itens/' + KEY, 400),
                                         ('/xml/manifestacoes/' + KEY, 400)])
@pytest.mark.parametrize('read_error', [RuntimeError('arquivo sintético vazio'), OSError('pasta privada')])
def test_secret_read_error_is_friendly_without_opening_database(tmp_path, monkeypatch, route, status, read_error):
    cfg = configured(tmp_path)

    def fail_password(self):
        raise read_error

    monkeypatch.setattr(type(cfg), 'current_database_password', fail_password)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        response = client.get(route)
    assert response.status_code == status and KEY not in response.text
    assert 'arquivo sintético vazio' not in response.text and 'pasta privada' not in response.text
    assert opened == closed == []


def test_password_is_read_once_in_worker_and_rotated_between_requests(tmp_path, monkeypatch):
    cfg = configured(tmp_path, encrypted=True)
    password_file = tmp_path / 'db-password.txt'
    correct = cfg.database_password
    password_file.write_text(correct, encoding='utf-8')
    cfg = replace(cfg, database_password_file=password_file)
    original = type(cfg).current_database_password
    reads = []

    def password(self):
        reads.append(get_ident())
        return original(self)

    monkeypatch.setattr(type(cfg), 'current_database_password', password)
    opened, closed, _ = observe(monkeypatch)
    caller = get_ident()
    with TestClient(create_app(cfg)) as client:
        first = client.get('/xml')
        assert KEY in first.text
        password_file.write_text('Senha incorreta', encoding='utf-8')
        assert KEY not in client.get('/xml').text
        password_file.write_text(correct, encoding='utf-8')
        assert KEY in client.get('/xml').text
    assert len(reads) == 3 and all(thread != caller for thread in reads)
    assert len(opened) == len(closed) == 2


def test_next_request_reads_new_committed_metadata(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    opened, closed, _ = observe(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        assert 'Atualizado entre pedidos' not in client.get('/xml').text
        with sqlite3.connect(cfg.database_path) as conn:
            conn.execute('UPDATE xml_documentos SET destinatario=? WHERE chave=?',
                         ('Atualizado entre pedidos', KEY))
        response = client.get('/xml')
        assert 'Atualizado entre pedidos' in response.text
        assert response.headers['cache-control'] == 'no-store'
    assert len(opened) == len(closed) == 2


def test_borrowed_report_and_corrections_do_not_close_callers_connection(tmp_path):
    from nfe_consulta.web.consulta_local import consultar_cartas_na_conexao
    cfg = configured(tmp_path)
    expected = xml_store.query_report(cfg.database_path, CNPJ_PADRAO)
    with sqlite3.connect(cfg.database_path) as conn:
        assert xml_store.query_report_na_conexao(conn, CNPJ_PADRAO) == expected
        assert consultar_cartas_na_conexao(conn, CNPJ_PADRAO, [KEY])[KEY][0].correcao == 'Texto da correção para conexão única'
        with pytest.raises(ValueError, match='Filtro do leitor inválido'):
            xml_store.query_report_na_conexao(conn, CNPJ_PADRAO, kind='invalido')
        assert conn.execute('SELECT 1').fetchone() == (1,)


@pytest.mark.parametrize('query', ['tipo=invalido', 'pagina=0', 'q=' + 'x' * 201,
                                  'situacao=invalida', 'item=' + 'x' * 201])
def test_invalid_main_filters_are_rejected_before_wrong_password_open(tmp_path, monkeypatch, query):
    cfg = replace(configured(tmp_path, encrypted=True), database_password='Senha incorreta')
    opened, closed, _ = observe(monkeypatch)
    calls = []
    from nfe_consulta.web import emissao_data
    original = emissao_data.abrir_banco

    def connect(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(emissao_data, 'abrir_banco', connect)
    with TestClient(create_app(cfg)) as client:
        response = client.get('/xml?' + query)
    assert response.status_code == 200 and 'Filtro do leitor inválido' in response.text
    assert 'Senha incorreta ou banco criptografado inválido' not in response.text
    assert calls == opened == closed == []

