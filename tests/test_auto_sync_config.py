from dataclasses import replace
from datetime import date
import asyncio
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from nfe_consulta.web.auto_sync_config import load_schedule, save_schedule, validate_schedule
from nfe_consulta.web.app import create_app
from nfe_consulta.web import scheduler
from nfe_consulta.web.auth import PUBLIC_USER, csrf_token

web = runpy.run_path(str(Path(__file__).with_name('test_web.py')))


def test_persisted_switch_overrides_startup_defaults(tmp_path):
    cfg = web['settings_web'](tmp_path)
    updated = save_schedule(cfg, enabled=True, times=['02:30','22:30','00:30'], weekdays=[4,0])
    assert updated.auto_sync_times == ('22:30','00:30','02:30')
    assert load_schedule(cfg).auto_sync_enabled
    assert load_schedule(cfg).auto_sync_weekdays == (0,4)
    save_schedule(updated, enabled=False, times=list(updated.auto_sync_times), weekdays=[0,4])
    restarted = load_schedule(replace(cfg, auto_sync_enabled=True))
    assert not restarted.auto_sync_enabled
    assert restarted.auto_sync_times == updated.auto_sync_times


@pytest.mark.parametrize('times,days', [(['04:00'],[0]),(['08:00'],[0]),(['21:59'],[0]),(['22:00','22:30'],[0]),(['22:00','22:00'],[0]),(['25:00'],[0]),(['22:00'],[]),(['22:00'],[7])])
def test_invalid_schedule_preserves_saved_configuration(tmp_path,times,days):
    cfg = web['settings_web'](tmp_path)
    save_schedule(cfg, enabled=False, times=['22:00'], weekdays=[0])
    path = cfg.database_path.parent/'auto-sync.json'
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        save_schedule(cfg,enabled=True,times=times,weekdays=days)
    assert path.read_bytes() == previous
    assert not load_schedule(cfg).auto_sync_enabled


def test_schedule_uses_selected_civil_days_and_minutes():
    # Segunda às 23:00: próximo disparo selecionado é terça 00:30.
    now = scheduler.horario_unico(date(2026,10,19),23,0)
    previous, following = scheduler.janelas_diarias(now, times=['00:30','22:30'], weekdays=[1])
    assert following == scheduler.horario_unico(date(2026,10,20),0,30)
    assert previous.weekday() == 1
    _, following = scheduler.janelas_diarias(scheduler.horario_unico(date(2026,10,20),23,0),times=['22:30'],weekdays=[1])
    assert following.date() == date(2026,10,27)


def test_admin_config_requires_auth_csrf_and_preserves_manual_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "agora_brasilia", lambda: scheduler.horario_unico(date(2026,10,19),12,0))
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    app = create_app(cfg)
    with TestClient(app) as client:
        data = {'enabled':'true','times':'22:30, 00:30, 02:30','weekdays':['0','1','2','3','4']}
        assert client.post('/admin/agendamento',data=data).status_code == 401
        csrf = web['entrar_admin'](client,cfg)
        assert client.post('/admin/agendamento',data={**data,'csrf':'wrong'}).status_code == 403
        assert client.post('/admin/agendamento',data={**data,'csrf':csrf,'times':'08:00'}).status_code == 400
        assert app.state.scheduler_settings == cfg
        assert client.post('/admin/agendamento',data={**data,'csrf':csrf},follow_redirects=False).status_code == 303
        assert app.state.scheduler_settings.auto_sync_enabled
        assert app.state.settings == cfg  # Janela e controles manuais não são modificados.
        page = client.get('/atualizar').text
        assert '22:00–04:00' in page
        assert '22:30, 00:30, 02:30' in page
        assert client.post('/admin/agendamento',data={k:v for k,v in {**data,'csrf':csrf}.items() if k!='enabled'},follow_redirects=False).status_code == 303
        assert not app.state.scheduler_settings.auto_sync_enabled
        assert not load_schedule(cfg).auto_sync_enabled
        assert 'Desativada' in client.get('/atualizar').text


def test_disabled_scheduler_does_not_consult_database_or_sefaz(monkeypatch):
    settings = SimpleNamespace(auto_sync_enabled=False,auto_sync_once_date=None)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings,auto_sync_next_at='old'))
    def forbidden(*args,**kwargs):
        pytest.fail('Agenda desativada não deve consultar banco nem SEFAZ')
    monkeypatch.setattr(scheduler,'proxima_recuperacao_nsu',forbidden)
    monkeypatch.setattr(scheduler,'sincronizar_configurado',forbidden)
    async def stop(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(scheduler,'_wait_schedule',stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop_sincronizacao_automatica(app))
    assert app.state.auto_sync_next_at is None
    assert asyncio.run(scheduler.executar_sincronizacao_automatica(app)) is None


def test_reconfiguration_is_observed_without_restarting_loop(monkeypatch):
    now = scheduler.horario_unico(date(2026,10,19),23,0)
    disabled = SimpleNamespace(auto_sync_enabled=False,auto_sync_once_date=None)
    enabled = SimpleNamespace(auto_sync_enabled=True,auto_sync_once_date=None,auto_sync_weekdays=(0,),auto_sync_times=('22:30','00:30'))
    app = SimpleNamespace(state=SimpleNamespace(settings=disabled,scheduler_settings=disabled))
    monkeypatch.setattr(scheduler,'agora_brasilia',lambda:now)
    monkeypatch.setattr(scheduler,'proxima_recuperacao_nsu',lambda _:None)
    monkeypatch.setattr(scheduler,'ultima_janela_concluida',lambda _:None)
    calls=[]
    async def execute(app, **kwargs):
        calls.append(kwargs['janela_agendada'])
    monkeypatch.setattr(scheduler,'executar_sincronizacao_automatica',execute)
    count=0
    async def wait(app, seconds):
        nonlocal count
        count+=1
        if count==1:
            app.state.scheduler_settings=enabled
        else:
            raise asyncio.CancelledError
    monkeypatch.setattr(scheduler,'_wait_schedule',wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop_sincronizacao_automatica(app))
    assert calls == [now.replace(hour=22,minute=30)]
