"""Leitura leve da Consulta e cache apenas dos recursos estáticos."""
from dataclasses import replace
from pathlib import Path
import runpy
from fastapi.testclient import TestClient
from nfe_consulta import __version__
from nfe_consulta.web import app as webapp

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))


def test_consulta_does_not_build_full_nsu_diagnostics(tmp_path, monkeypatch):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    web['criar_banco'](cfg.database_path)
    import sqlite3
    from nfe_consulta.config import CNPJ_PADRAO
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute('INSERT INTO estado_distribuicao(cnpj, ult_nsu, max_nsu, atualizado_em) VALUES (?, ?, ?, ?)',
                     (CNPJ_PADRAO, '1'.zfill(15), '1'.zfill(15), '2026-11-04 03:00:00'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Consulta não deve montar diagnóstico completo de NSUs')
    monkeypatch.setattr(webapp, '_status_web', forbidden)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get('/consulta')
        assert response.status_code == 200
        assert 'Banco disponível' in response.text
        assert response.headers['cache-control'] == 'no-store'


def test_versioned_assets_can_be_reused_but_pages_remain_fresh(tmp_path):
    cfg = replace(web['settings_web'](tmp_path), local_auth_enabled=False)
    with TestClient(webapp.create_app(cfg)) as client:
        response = client.get(f'/static/app.css?v={__version__}-layout')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
        assert client.get('/static/app.css').headers['cache-control'] == 'public, max-age=0, must-revalidate'
        assert client.get('/downloads').headers['cache-control'] == 'no-store'


def test_certificate_display_cache_refreshes_on_file_password_and_time_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    cfg = web['settings_web'](tmp_path)
    pfx = tmp_path / 'certificate.pfx'
    pfx.write_bytes(b'fixture')
    config = SimpleNamespace(path=pfx, password='initial')
    monkeypatch.setattr(webapp, 'carregar_config_certificado_arquivo', lambda *args: config)
    now = [300]
    monkeypatch.setattr(webapp.time, 'monotonic', lambda: now[0])
    calls = []
    def load(path, password):
        calls.append((path, password))
        return SimpleNamespace(subject='Certificado', valid_to='2030-01-01')
    monkeypatch.setattr(webapp, 'carregar_certificado_arquivo', load)
    webapp._certificado_arquivo_cached.cache_clear()
    try:
        assert webapp._certificado_web(cfg)['ready']
        assert webapp._certificado_web(cfg)['ready']
        assert len(calls) == 1
        pfx.write_bytes(b'changed fixture')
        assert webapp._certificado_web(cfg)['ready']
        assert len(calls) == 2
        config.password = 'changed'
        webapp._certificado_web(cfg)
        assert len(calls) == 3
        now[0] += 30
        webapp._certificado_web(cfg)
        assert len(calls) == 4
        pfx.unlink()
        assert not webapp._certificado_web(cfg)['ready']
    finally:
        webapp._certificado_arquivo_cached.cache_clear()
