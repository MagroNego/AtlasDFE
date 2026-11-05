"""Prontidão e leitura da página na mesma conexão local por requisição."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web.consulta_local import (
    FiltrosEventos, ResultadoEventos, consultar_eventos_na_conexao,
)


def banco_indisponivel() -> dict:
    return dict(ready=False, label='Banco indisponível',
                notice='O banco de manifestações ainda não está disponível para consulta.')


def banco_nao_sincronizado(*, com_erro=False) -> dict:
    return dict(ready=False, label='Banco não sincronizado',
                notice=('O banco de manifestações ainda não está configurado ou sincronizado para consulta.'
                        if com_erro else 'O banco de manifestações ainda não possui uma sincronização concluída.'))


def estado_consulta(conexao, cnpj: str) -> dict:
    tables = {row[0] for row in conexao.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'estado_distribuicao' in tables:
        row = conexao.execute('SELECT atualizado_em FROM estado_distribuicao WHERE cnpj=?', (cnpj,)).fetchone()
        if row and row[0] is not None:
            datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
            return dict(ready=True, label='Banco disponível', notice=None)
    for table in ('xml_documentos', 'xml_cancelamentos', 'cartas_correcao'):
        if table in tables and conexao.execute(f'SELECT 1 FROM {table} WHERE cnpj=? LIMIT 1', (cnpj,)).fetchone():
            return dict(ready=True, label='Banco local disponível', notice=None)
    return banco_nao_sincronizado()


@dataclass(frozen=True)
class ConsultaData:
    database_state: dict
    resultado: ResultadoEventos | None = None
    error: Exception | None = None


def consultar_pagina(database_path: Path, cnpj: str, filtros: FiltrosEventos, *,
                     password: str | None = None, pagina: int = 1,
                     password_provider: Callable[[], str | None] | None = None) -> ConsultaData:
    """Abre, usa e fecha a conexão dentro da mesma chamada de trabalho."""
    if not Path(database_path).is_file():
        return ConsultaData(banco_indisponivel())
    try:
        if password_provider is not None:
            password = password_provider()
        conexao = abrir_banco(database_path, password, somente_leitura=True)
        try:
            state = estado_consulta(conexao, cnpj)
            if not state['ready']:
                return ConsultaData(state)
            try:
                result = consultar_eventos_na_conexao(conexao, cnpj, filtros,
                    pagina=pagina, por_pagina=100, agrupar_cartas=True)
            except Exception as exc:
                return ConsultaData(state, error=exc)
            return ConsultaData(state, resultado=result)
        finally:
            conexao.close()
    except Exception:
        return ConsultaData(banco_nao_sincronizado(com_erro=True))
