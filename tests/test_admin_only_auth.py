"""Acesso comum livre, sem conceder privilégios administrativos pela rede."""
from dataclasses import replace
from pathlib import Path
import runpy
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from nfe_consulta.web.app import create_app
from nfe_consulta.web.auth import PUBLIC_USER, csrf_token

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))


def setup(tmp_path, **changes):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False, **changes)
    web['criar_banco'](cfg.database_path)
    from nfe_consulta.banco import BancoManifestacoes
    from nfe_consulta.config import CNPJ_PADRAO
    from nfe_consulta.modelos import Manifestacao, RetornoDistribuicao
    banco = BancoManifestacoes(str(cfg.database_path))
    banco.salvar_retorno(CNPJ_PADRAO, RetornoDistribuicao(138, 'ok', '1'.zfill(15), '1'.zfill(15),
        ((web['CHAVE'], Manifestacao('210240', 'Operação não Realizada', '2026-10-01T10:00:00-03:00', '1')),), 0))
    banco.fechar()
    return cfg, create_app(cfg)


@pytest.mark.parametrize('path', ['/consulta', '/status', '/xml', '/downloads', '/consulta/exportar', '/xml/exportar?formato=csv'])
def test_common_access_without_password_does_not_grant_admin(tmp_path, path):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200
        assert not response.cookies
        assert not client.cookies
        assert client.get('/atualizar', follow_redirects=False).status_code == 303
        html = client.get('/consulta').text
        assert 'Navegação principal' in html
        assert '>Emissão</a>' in html
        assert '>Sair do Admin</button>' not in html
        assert 'Alterar situação de notas' not in html
        assert 'Criar conta' not in html


def test_direct_admin_mutations_and_manual_status_are_blocked(tmp_path):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        for route in app.routes:
            path = getattr(route, 'path', '')
            if 'POST' not in getattr(route, 'methods', set()):
                continue
            if (path.startswith(('/atualizar', '/admin/')) and path not in {'/admin/login', '/admin/logout', '/admin/contas'}) or path == '/xml/importar':
                assert client.post(path, data={'csrf': csrf_token(PUBLIC_USER, cfg)}).status_code == 401, path
        for action in ['cancelar', 'remover']:
            response = client.post('/admin/notas/situacao', data={
                'csrf': csrf_token(PUBLIC_USER, cfg), 'notas': '124', 'motivo': 'Teste', 'acao': action})
            assert response.status_code == 401
        assert app.state.admin_accounts is not None


def test_admin_login_csrf_logout_and_stolen_old_cookie(tmp_path):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        page = client.get('/admin/login')
        assert page.status_code == 200
        assert 'Entrar no Admin' in page.text
        assert 'Criar conta' not in page.text
        credentials = {'username': cfg.admin_username, 'password': cfg.admin_password}
        assert client.post('/admin/login', data={**credentials, 'csrf': 'errado'}).status_code == 403
        token = web['entrar_admin'](client, cfg)
        cookie = client.cookies[cfg.admin_cookie_name]
        assert client.get('/atualizar').status_code == 200
        assert 'Alterar situação de notas' in client.get('/xml/importar').text
        assert client.post('/admin/notas/situacao', data={'csrf': 'errado', 'notas': '124', 'motivo': 'Teste'}).status_code == 403
        assert client.post('/admin/logout', data={'csrf': token}, follow_redirects=False).status_code == 303
        client.cookies.set(cfg.admin_cookie_name, cookie)
        assert client.get('/atualizar', follow_redirects=False).status_code == 303
        assert client.get('/consulta').status_code == 200


@pytest.mark.parametrize('role', ['fiscal', 'consulta'])
def test_old_common_accounts_and_sessions_do_not_authenticate_admin(tmp_path, role):
    cfg, app = setup(tmp_path)
    accounts = app.state.admin_accounts
    accounts.create('antigo', 'Senha-comum-antiga-123!', role)
    old_cookie = accounts.new_session('antigo')
    with TestClient(app) as client:
        response = client.post('/admin/login', data={'csrf': csrf_token(PUBLIC_USER, cfg),
            'username': 'antigo', 'password': 'Senha-comum-antiga-123!'}, follow_redirects=False)
        assert response.status_code == 401
        assert not response.cookies
        client.cookies.set(cfg.admin_cookie_name, old_cookie)
        assert client.get('/atualizar', follow_redirects=False).status_code == 303
        assert 'Sair' not in client.get('/consulta').text
    assert accounts.authenticate('antigo', 'Senha-comum-antiga-123!') == 'antigo'


def test_missing_admin_configuration_keeps_public_access_and_admin_locked(tmp_path):
    cfg, app = setup(tmp_path, admin_password=None)
    with TestClient(app) as client:
        assert client.get('/consulta').status_code == 200
        page = client.get('/admin/login')
        assert page.status_code == 200
        assert 'ainda não foi configurado' in page.text
        assert client.post('/admin/login', data={'csrf': csrf_token(PUBLIC_USER, cfg),
            'username': 'admin', 'password': 'qualquer'}).status_code == 503
        assert client.post('/admin/notas/situacao', data={}).status_code == 401


@pytest.mark.parametrize('path', ['/cadastro', '/admin/contas'])
def test_account_creation_endpoints_disabled_in_admin_only_mode(tmp_path, path):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        assert client.get(path, follow_redirects=False).status_code == 404
        assert client.post(path, data={}).status_code == 404


def test_admin_failed_login_is_throttled(tmp_path):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        data = {'csrf': csrf_token(PUBLIC_USER, cfg), 'username': 'admin', 'password': 'errada'}
        for _ in range(10):
            assert client.post('/admin/login', data=data).status_code == 401
        assert client.post('/admin/login', data=data).status_code == 429
        assert client.get('/consulta').status_code == 200


def test_idle_expired_and_reset_admin_sessions_lose_privileges(tmp_path):
    cfg, app = setup(tmp_path)
    accounts = app.state.admin_accounts
    with TestClient(app) as client:
        web['entrar_admin'](client, cfg)
        with sqlite3.connect(accounts.path) as db:
            db.execute('UPDATE admin_sessions SET last_seen=?', (int(time.time()) - 1801,))
        assert client.get('/atualizar', follow_redirects=False).status_code == 303
        web['entrar_admin'](client, cfg)
        accounts.reset_password('admin', 'Nova-senha-administrativa-123!')
        assert client.post('/admin/notas/situacao', data={}).status_code == 401
        assert client.get('/consulta').status_code == 200


@pytest.mark.parametrize('cookie', ['acesso-rede', 'inventado', 'x' * 129])
def test_forged_cookie_and_identity_headers_never_grant_admin(tmp_path, cookie):
    cfg, app = setup(tmp_path)
    with TestClient(app) as client:
        client.cookies.set(cfg.admin_cookie_name, cookie)
        response = client.get('/atualizar', follow_redirects=False, headers={
            cfg.user_header: 'admin', cfg.groups_header: 'admin', cfg.proxy_secret_header: cfg.proxy_secret})
        assert response.status_code == 303
        response = client.get('/consulta')
        assert response.status_code == 200
        assert not response.cookies


@pytest.mark.parametrize('change', ['expired', 'inactive'])
def test_absolute_expiration_and_deactivation_revoke_admin_access(tmp_path, change):
    cfg, app = setup(tmp_path)
    accounts = app.state.admin_accounts
    with TestClient(app) as client:
        web['entrar_admin'](client, cfg)
        if change == 'expired':
            with sqlite3.connect(accounts.path) as db:
                db.execute('UPDATE admin_sessions SET expires_at=?', (int(time.time()) - 1,))
        else:
            accounts.create('outro-admin', 'Senha-admin-alternativo-123!', 'admin')
            accounts.set_active('admin', False)
        assert client.post('/admin/notas/situacao', data={}).status_code == 401
        assert client.get('/consulta').status_code == 200


def test_manual_cancel_and_undo_work_only_after_admin_login(tmp_path):
    h = runpy.run_path(str(Path(__file__).with_name('test_cancelamento_manual.py')))
    cfg = replace(h['h']['setup'](tmp_path), local_auth_enabled=False)
    with TestClient(create_app(cfg)) as client:
        data = {'notas': '124', 'motivo': 'Correção conferida', 'acao': 'cancelar'}
        assert client.post('/admin/notas/situacao', data=data).status_code == 401
        token = web['entrar_admin'](client, cfg)
        response = client.post('/admin/notas/situacao', data={**data, 'csrf': token})
        assert response.status_code == 200
        assert '1 nota(s) marcada(s)' in response.text
        response = client.post('/admin/notas/situacao', data={**data, 'csrf': token, 'acao': 'remover'})
        assert response.status_code == 200
        assert 'Marcação manual removida' in response.text
