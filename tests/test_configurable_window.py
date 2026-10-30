from dataclasses import replace
from datetime import datetime

import pytest

from nfe_consulta import janela_consulta as janela
from nfe_consulta.web.auto_sync_config import validate_schedule, save_schedule, load_schedule


@pytest.mark.parametrize('start,end,instant,expected', [
    ('22:00', '04:00', '2026-10-30T21:59:00-03:00', '2026-10-30T22:00:00-03:00'),
    ('22:00', '04:00', '2026-10-30T04:00:00-03:00', '2026-10-30T22:00:00-03:00'),
    ('09:15', '17:45', '2026-10-30T09:15:00-03:00', '2026-10-30T09:15:00-03:00'),
    ('09:15', '17:45', '2026-10-30T17:45:00-03:00', '2026-10-31T09:15:00-03:00'),
    ('23:30', '02:15', '2026-10-30T02:14:00-03:00', '2026-10-30T02:14:00-03:00'),
])
def test_window_boundaries(start, end, instant, expected):
    with janela.usar_janela(lambda: janela.JanelaConsulta(start, end)):
        assert janela.proxima_abertura(datetime.fromisoformat(instant)).isoformat() == expected


def test_live_provider_rechecks_window_and_resets_context():
    active = [janela.JanelaConsulta('09:00', '17:00')]
    now = datetime.fromisoformat('2026-10-30T12:00:00-03:00')
    with janela.usar_janela(lambda: active[0]):
        assert janela.proxima_abertura(now) == now
        active[0] = janela.JanelaConsulta('22:00', '04:00')
        assert janela.proxima_abertura(now).hour == 22
    assert janela.janela_atual() == janela.JanelaConsulta()


def test_custom_schedule_validates_window_and_real_weekly_spacing():
    values = validate_schedule(True, ['10:00', '09:00'], [0], window_start='09:00', window_end='17:00')
    assert values['auto_sync_times'] == ('09:00', '10:00')
    with pytest.raises(ValueError):
        validate_schedule(True, ['17:00'], [0], window_start='09:00', window_end='17:00')
    with pytest.raises(ValueError):
        validate_schedule(True, ['23:50', '00:10'], [0, 1], window_start='00:05', window_end='23:55')
    validate_schedule(True, ['23:50', '00:10'], [0, 2], window_start='00:05', window_end='23:55')


@pytest.mark.parametrize('start,end', [('04:00', '04:00'), ('24:00', '04:00'), ('1:00', '04:00')])
def test_invalid_windows(start, end):
    with pytest.raises(ValueError):
        janela.JanelaConsulta(start, end)


def test_window_roundtrip_and_invalid_write_preserves_file(tmp_path):
    from nfe_consulta.web.settings import get_settings
    cfg = replace(get_settings(), auto_sync_config_path=tmp_path / 'schedule.json')
    saved = save_schedule(cfg, enabled=True, times=['09:00'], weekdays=[0], window_start='09:00', window_end='17:00')
    assert load_schedule(cfg).sync_window_end == '17:00'
    before = cfg.auto_sync_config_path.read_bytes()
    with pytest.raises(ValueError):
        save_schedule(saved, enabled=True, times=['18:00'], weekdays=[0])
    assert cfg.auto_sync_config_path.read_bytes() == before


def test_admin_window_updates_status_without_changing_cursor(tmp_path, monkeypatch):
    import runpy
    from pathlib import Path
    from fastapi.testclient import TestClient
    from nfe_consulta.web.app import create_app
    from nfe_consulta.seguranca_banco import abrir_banco
    from nfe_consulta.config import CNPJ_PADRAO

    helpers = runpy.run_path(str(Path(__file__).with_name('test_web.py')))
    cfg = helpers['settings_web'](tmp_path)
    helpers['criar_banco'](cfg.database_path)
    monkeypatch.setattr(janela, 'agora_brasilia', lambda: datetime.fromisoformat('2026-10-30T12:00:00-03:00'))
    app = create_app(cfg)
    with TestClient(app) as client:
        csrf = helpers['entrar_admin'](client, cfg)
        conn = abrir_banco(cfg.database_path, somente_leitura=True)
        try:
            before = conn.execute('SELECT * FROM estado_distribuicao').fetchall()
        finally:
            conn.close()
        data = dict(csrf=csrf, times='09:00, 10:00', weekdays=['0'], window_start='09:00', window_end='17:00')
        assert client.post('/admin/agendamento', data={**data, 'csrf': 'bad'}).status_code == 403
        assert client.post('/admin/agendamento', data=data, follow_redirects=False).status_code == 303
        assert app.state.scheduler_settings.sync_window_start == '09:00'
        page = client.get('/atualizar').text
        assert 'name="window_start" value="09:00"' in page
        assert 'name="window_end" value="17:00"' in page
        assert '09:00–17:00' in page
        assert '09h às 17h' in client.get('/status').text
        assert client.post('/admin/agendamento', data={**data, 'times': '18:00'}).status_code == 400
        assert app.state.scheduler_settings.sync_window_end == '17:00'
        conn = abrir_banco(cfg.database_path, somente_leitura=True)
        try:
            assert conn.execute('SELECT * FROM estado_distribuicao').fetchall() == before
        finally:
            conn.close()
    assert load_schedule(cfg).sync_window_start == '09:00'


def test_worker_observes_new_window_between_calls(tmp_path, monkeypatch):
    from nfe_consulta.web.settings import get_settings
    from nfe_consulta.web import sync_runtime
    cfg = replace(get_settings(), auto_sync_config_path=tmp_path / 'schedule.json')
    save_schedule(cfg, enabled=False, times=['09:00'], weekdays=[0], window_start='09:00', window_end='17:00')
    monkeypatch.setattr(janela, 'agora_brasilia', lambda: datetime.fromisoformat('2026-10-30T12:00:00-03:00'))
    def work(*args, **kwargs):
        janela.exigir_janela()
        save_schedule(cfg, enabled=False, times=['22:00'], weekdays=[0], window_start='22:00', window_end='04:00')
        janela.exigir_janela()
    monkeypatch.setattr(sync_runtime, '_sincronizar_configurado', work)
    with pytest.raises(janela.NfeJanelaConsultaErro):
        sync_runtime.sincronizar_configurado(cfg)


@pytest.mark.parametrize('persistence_failure', [False, True])
def test_retry_uses_current_schedule_after_window_change(tmp_path, monkeypatch, persistence_failure):
    import asyncio
    from threading import Lock
    from types import SimpleNamespace
    from nfe_consulta.web import scheduler
    from nfe_consulta.web.settings import get_settings
    old = replace(get_settings(), auto_sync_enabled=True)
    new = replace(old, sync_window_start='09:00', sync_window_end='17:00', auto_sync_times=('09:00', '16:00'))
    now = datetime.fromisoformat('2026-10-30T12:00:00-03:00')
    app = SimpleNamespace(state=SimpleNamespace(settings=old, scheduler_settings=old,
        sync_lock=Lock(), audit=SimpleNamespace(write_system=lambda *a, **k: None)))
    monkeypatch.setattr(scheduler, 'agora_brasilia', lambda: now)
    def sync(*args, **kwargs):
        app.state.scheduler_settings = new
        if not persistence_failure:
            raise RuntimeError('Falha simulada')
        return SimpleNamespace(completo=True)
    def failed_persistence(*args):
        raise RuntimeError('Falha de persistência simulada')
    monkeypatch.setattr(scheduler, 'sincronizar_configurado', sync)
    monkeypatch.setattr(scheduler, 'registrar_execucao_agendada', failed_persistence)
    assert asyncio.run(scheduler.executar_sincronizacao_automatica(app)) == now.replace(hour=16)
    assert not app.state.sync_lock.locked()
