"""Contagem agrupada preserva eventos principais e uma linha por nota só com CC-e."""
import sqlite3

import pytest

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.web import consulta_local as query

CNPJ = '16840128000101'


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'eventos.db'
    banco = BancoManifestacoes(str(path))
    banco.fechar()
    events = [(1, '210210', '01'), (1, '210200', '02'), (1, '110110', '03'),
              (2, '110110', '01'), (2, '110110', '03'),
              (3, '210210', '01'), (3, '110110', '02'), (3, '110110', '03'),
              (4, '210210', '02'), (5, '110110', '03')]
    with sqlite3.connect(path) as conn:
        conn.executemany('''INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,?,'Evento',?,?,?,'teste')''',
            [(CNPJ, f'332611{CNPJ}55001{number:09d}1000000000', code,
              f'2026-11-{day}', str(i), str(i))
             for i, (number, code, day) in enumerate(events, 1)])
    return path


def test_grouped_count_reads_source_once(database, monkeypatch):
    original = query.abrir_banco
    statements = []

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(query, 'abrir_banco', connect)
    result = query.consultar_eventos(database, CNPJ, query.FiltrosEventos(), agrupar_cartas=True)
    assert result.total == 6
    count_sql = next(sql for sql in statements
                     if 'COUNT(*)' in sql or 'COALESCE(SUM(' in sql)
    with sqlite3.connect(database) as conn:
        plan = conn.execute('EXPLAIN QUERY PLAN ' + count_sql).fetchall()
    assert sum('SEARCH manifestacoes ' in row[3] for row in plan) == 1
    assert not any('LIST SUBQUERY' in row[3] for row in plan)


@pytest.mark.parametrize('filters,expected', [
    ({}, 6),
    ({'codigo': '110110'}, 4),
    ({'codigo': '210210'}, 3),
    ({'numero': '2'}, 1),
    ({'numero': '1'}, 2),
    ({'numero': '99'}, 0),
    ({'data_inicial': '2026-11-02'}, 5),
    ({'data_inicial': '2026-11-03'}, 4),
])
def test_grouped_count_matches_legacy_and_pagination(database, filters, expected):
    filters = query.normalizar_filtros(**filters)
    where, params = query._where_eventos(CNPJ, filters)
    legacy = f'''WITH filtrados AS (SELECT * FROM manifestacoes WHERE {where}),
        visiveis AS (SELECT * FROM filtrados WHERE codigo!='110110' OR
        (codigo='110110' AND chave NOT IN (SELECT chave FROM filtrados WHERE codigo!='110110')
         AND id IN (SELECT MIN(id) FROM filtrados WHERE codigo='110110' GROUP BY chave)))
        SELECT COUNT(*) FROM visiveis'''
    with sqlite3.connect(database) as conn:
        assert conn.execute(legacy, params).fetchone()[0] == expected
    pages = [query.consultar_eventos(database, CNPJ, filters, agrupar_cartas=True,
                                    pagina=p, por_pagina=1) for p in range(1, max(expected, 1) + 1)]
    assert all(result.total == expected for result in pages)
    assert sum(len(result.eventos) for result in pages) == expected
