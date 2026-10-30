"""Política de consultas externas isolada por execução; banco local sempre disponível."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re

from nfe_consulta.modelos import NfeLimiteConsultaErro

BRASILIA = timezone(timedelta(hours=-3))


class NfeJanelaConsultaErro(NfeLimiteConsultaErro):
    pass


@dataclass(frozen=True)
class JanelaConsulta:
    inicio: str = '22:00'
    fim: str = '04:00'

    def __post_init__(self):
        for value in (self.inicio, self.fim):
            if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
                raise ValueError('Use horários da janela no formato HH:MM.')
        if self.inicio == self.fim:
            raise ValueError('O início e o fim da janela devem ser diferentes.')

    def contem(self, instante):
        horario = instante.astimezone(BRASILIA).strftime('%H:%M')
        if self.inicio < self.fim:
            return self.inicio <= horario < self.fim
        return horario >= self.inicio or horario < self.fim

    @property
    def rotulo(self):
        def label(value):
            return value[:2] + 'h' + (value[3:] if value[3:] != '00' else '')
        return f'{label(self.inicio)} às {label(self.fim)}'


_provider = ContextVar('janela_consulta_provider', default=None)


def janela_de(settings):
    if isinstance(settings, JanelaConsulta):
        return settings
    return JanelaConsulta(getattr(settings, 'sync_window_start', '22:00'),
                          getattr(settings, 'sync_window_end', '04:00'))


def janela_atual():
    provider = _provider.get()
    return janela_de(provider()) if provider is not None else JanelaConsulta()


@contextmanager
def usar_janela(provider):
    token = _provider.set(provider)
    try:
        yield
    finally:
        _provider.reset(token)


def agora_brasilia():
    return datetime.now(BRASILIA)


def inicio_janela(instante):
    if instante.tzinfo is None:
        raise ValueError('O instante deve ter fuso horário')
    agora = instante.astimezone(BRASILIA)
    janela = janela_atual()
    inicio = datetime.combine(agora.date(), time.fromisoformat(janela.inicio), tzinfo=BRASILIA)
    if inicio > agora:
        inicio -= timedelta(days=1)
    return inicio


def proxima_abertura(instante=None):
    agora = instante if instante is not None else agora_brasilia()
    if agora.tzinfo is None:
        raise ValueError('O instante deve ter fuso horário')
    agora = agora.astimezone(BRASILIA)
    janela = janela_atual()
    if janela.contem(agora):
        return agora
    abertura = datetime.combine(agora.date(), time.fromisoformat(janela.inicio), tzinfo=BRASILIA)
    return abertura if abertura > agora else abertura + timedelta(days=1)


def exigir_janela():
    agora = agora_brasilia()
    if proxima_abertura(agora) > agora:
        raise NfeJanelaConsultaErro(
            f'Consultas à SEFAZ permitidas das {janela_atual().rotulo} (horário de Brasília). '
            'A consulta ao banco local continua disponível.'
        )
