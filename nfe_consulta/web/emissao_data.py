"""Leitura de páginas de Emissão com uma conexão local por pedido."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web import xml_store as store
from nfe_consulta.web.consulta_local import (
    ResultadoEventos, consultar_cartas_na_conexao, consultar_eventos_na_conexao,
    normalizar_filtros,
)


class NotaNaoEncontrada(LookupError):
    pass


@dataclass(frozen=True)
class EmissaoPagina:
    result: dict
    cartas_correcao: dict
    error: Exception | None = None


def _password(password_provider, *, detalhe=False):
    try:
        return password_provider()
    except (RuntimeError, OSError) as exc:
        if detalhe:
            raise ValueError('Não foi possível ler a senha do banco configurada.') from exc
        raise


@contextmanager
def _conexao(database_path, password):
    conn = abrir_banco(database_path, password, somente_leitura=True)
    try:
        yield conn
    finally:
        conn.close()


def carregar_emissao(database_path, cnpj, *, password_provider, **filters):
    password = _password(password_provider)
    empty = store._empty_report(filters.get('kind', 'notas'), filters.get('query', ''),
        filters.get('page', 1), filters.get('situacao', ''),
        filters.get('item_query', ''), filters.get('note_key', ''))
    if not Path(database_path).is_file():
        return EmissaoPagina(empty, {})
    with _conexao(database_path, password) as conn:
        result = store.query_report_na_conexao(conn, cnpj, **filters)
        if filters.get('kind', 'notas') != 'notas':
            return EmissaoPagina(result, {})
        try:
            cartas = consultar_cartas_na_conexao(conn, cnpj,
                [row['Chave Acesso'] for row in result['rows']])
        except Exception as exc:
            # A listagem já lida permanece visível se o enriquecimento falhar.
            return EmissaoPagina(result, {}, error=exc)
        return EmissaoPagina(result, cartas)


def _nota(conn, cnpj, chave):
    note = store.query_report_na_conexao(conn, cnpj, note_key=chave)
    if not note['rows']:
        raise NotaNaoEncontrada('Nota não encontrada no banco local.')
    return note


def carregar_itens(database_path, cnpj, chave, *, password_provider, item='', pagina=1):
    password = _password(password_provider, detalhe=True)
    if not Path(database_path).is_file():
        raise NotaNaoEncontrada('Nota não encontrada no banco local.')
    with _conexao(database_path, password) as conn:
        note = _nota(conn, cnpj, chave)
        result = store.query_report_na_conexao(conn, cnpj, kind='itens', note_key=chave,
            item_query=item, page=pagina)
        return note, result


def carregar_manifestacoes(database_path, cnpj, chave, *, password_provider,
                           data_inicial='', data_final='', codigo='', pagina=1):
    password = _password(password_provider, detalhe=True)
    if not Path(database_path).is_file():
        raise NotaNaoEncontrada('Nota não encontrada no banco local.')
    with _conexao(database_path, password) as conn:
        note = _nota(conn, cnpj, chave)
        selected = normalizar_filtros(chave=chave, data_inicial=data_inicial,
            data_final=data_final, codigo=codigo)
        if pagina < 1:
            raise ValueError('Página inválida.')
        has_events = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manifestacoes'").fetchone()
        result = (consultar_eventos_na_conexao(conn, cnpj, selected, pagina=pagina, agrupar_cartas=True)
                  if has_events else ResultadoEventos((), 0, 1, 100))
        return note, result, selected
