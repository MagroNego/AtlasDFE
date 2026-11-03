"""Acesso comum livre e Administração com senha no modo padrão."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import runpy

from fastapi.testclient import TestClient

from nfe_consulta import janela_consulta
from nfe_consulta.web.app import create_app
from nfe_consulta.web.auth import PUBLIC_USER, csrf_token

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))


def test_default_settings_load_admin_credentials_without_enabling_user_login(monkeypatch):
    from nfe_consulta.web import settings
    monkeypatch.delenv('NFE_WEB_LOCAL_AUTH', raising=False)
    monkeypatch.setenv('NFE_WEB_ENV', 'development')
    monkeypatch.delenv('NFE_WEB_CORPORATE_AUTH', raising=False)
    monkeypatch.setattr(settings, '_senha_admin', lambda: 'Senha-administrativa-123!')
    monkeypatch.setattr(settings, '_usuario_admin', lambda: 'administrador')
    settings.get_settings.cache_clear()
    try:
        cfg = settings.get_settings()
        assert not cfg.local_auth_enabled
        assert cfg.admin_only_auth
        assert cfg.admin_password == 'Senha-administrativa-123!'
        assert cfg.admin_username == 'administrador'
    finally:
        settings.get_settings.cache_clear()


def test_network_access_has_no_user_login_or_account_management(tmp_path):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    web['criar_banco'](cfg.database_path)
    accounts = cfg.database_path.parent / 'admin_accounts.db'
    from nfe_consulta.web.admin_accounts import AdminAccounts
    existing = AdminAccounts(accounts, bootstrap_username='admin', bootstrap_password=cfg.admin_password)
    existing.create('antigo', 'Senha-usuario-antigo-123!', 'fiscal')
    app = create_app(cfg)
    with TestClient(app) as client:
        for path in ('/consulta', '/status', '/xml', '/downloads'):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 200, path
            assert 'Criar conta' not in response.text
            assert 'Gerenciar usuários' not in response.text
            assert 'logout-form' not in response.text
            assert not response.cookies
        assert client.get('/admin/login').status_code == 200
        assert client.get('/atualizar', follow_redirects=False).status_code == 303
        for path in ('/cadastro', '/admin/contas'):
            assert client.get(path).status_code == 404
            assert client.post(path, data={}).status_code == 404
    assert app.state.admin_accounts.authenticate('antigo', 'Senha-usuario-antigo-123!') == 'antigo'


def test_authenticated_network_admin_preserves_csrf_schedule_and_sefaz_window(tmp_path, monkeypatch):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    web['criar_banco'](cfg.database_path)
    app = create_app(cfg)
    def forbidden(*args, **kwargs):
        raise AssertionError('Consulta real à SEFAZ não permitida no teste')
    monkeypatch.setattr('nfe_consulta.web.app.sincronizar_configurado', forbidden)
    monkeypatch.setattr(janela_consulta, 'agora_brasilia',
        lambda: datetime(2026, 10, 19, 12, tzinfo=janela_consulta.BRASILIA))
    with TestClient(app) as client:
        data = {'times': '22:30, 00:30', 'weekdays': ['0'], 'enabled': 'true'}
        assert client.post('/admin/agendamento', data={**data, 'csrf': csrf_token(PUBLIC_USER, cfg)}).status_code == 401
        token = web['entrar_admin'](client, cfg)
        assert client.post('/admin/agendamento', data={**data, 'csrf': 'errado'}).status_code == 403
        assert not (cfg.database_path.parent / 'auto-sync.json').exists()
        assert client.post('/admin/agendamento', data={**data, 'csrf': token}, follow_redirects=False).status_code == 303
        assert app.state.scheduler_settings.auto_sync_times == ('22:30', '00:30')
        assert client.post('/atualizar/sincronizar', data={
            'csrf': token, 'confirmacao': 'sincronizar', 'max_lotes': '1'}).status_code == 429
