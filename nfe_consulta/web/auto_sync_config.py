"""Configuração persistente da agenda, e da janela operacional de consultas."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile
from nfe_consulta.janela_consulta import JanelaConsulta

DEFAULT_TIMES = ('22:00', '23:00', '00:00', '01:00', '02:00', '03:00')


def validate_schedule(enabled, times, weekdays, *, window_start='22:00', window_end='04:00'):
    window = JanelaConsulta(window_start, window_end)
    if type(enabled) is not bool:
        raise ValueError('Estado do agendamento inválido.')
    if not isinstance(times, (list, tuple)) or not 1 <= len(times) <= 6:
        raise ValueError('Informe de um a seis horários.')
    if not isinstance(weekdays, (tuple, list)) or not weekdays or any(type(day) is not int or day not in range(7) for day in weekdays):
        raise ValueError('Selecione pelo menos um dia válido da semana.')
    minutes = []
    start = int(window_start[:2])*60 + int(window_start[3:])
    end = int(window_end[:2])*60 + int(window_end[3:])
    for value in times:
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
            raise ValueError('Use horários no formato HH:MM, separados por vírgula.')
        hour, minute = map(int, value.split(':'))
        value_minutes = hour*60 + minute
        if (value_minutes - start) % 1440 >= (end - start) % 1440:
            raise ValueError(f'Os disparos devem ocorrer das {window.rotulo}, antes do fechamento, Brasília.')
        minutes.append(value_minutes)
    weekly = sorted(day*1440 + minute for day in set(weekdays) for minute in minutes)
    if any(b-a < 60 for a,b in zip(weekly, weekly[1:] + [weekly[0]+10080])):
        raise ValueError('Mantenha pelo menos 60 minutos entre os horários, sem duplicatas.')
    ordered = tuple(f'{minute//60:02d}:{minute%60:02d}' for minute in sorted(minutes, key=lambda m: (m-start)%1440))
    return dict(auto_sync_enabled=enabled, auto_sync_times=ordered,
                auto_sync_weekdays=tuple(sorted(set(weekdays))),
                sync_window_start=window_start, sync_window_end=window_end)


def config_path(settings):
    return settings.auto_sync_config_path or settings.database_path.parent / 'auto-sync.json'


def load_schedule(settings):
    path = config_path(settings)
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text('utf-8'))
        values = validate_schedule(data['enabled'], data['times'], data['weekdays'],
            window_start=data.get('window_start', '22:00'), window_end=data.get('window_end', '04:00'))
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Configuração de agendamento inválida em auto-sync.json.') from exc
    return replace(settings, **values)


def save_schedule(settings, *, enabled, times, weekdays, window_start=None, window_end=None):
    values = validate_schedule(enabled, times, weekdays,
        window_start=window_start if window_start is not None else settings.sync_window_start,
        window_end=window_end if window_end is not None else settings.sync_window_end)
    updated = replace(settings, **values)
    path = Path(config_path(settings))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.auto-sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(dict(enabled=enabled, times=values['auto_sync_times'], weekdays=values['auto_sync_weekdays'],
                           window_start=values['sync_window_start'], window_end=values['sync_window_end']), stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return updated
