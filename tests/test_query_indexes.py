"""Limites de trabalho SQL independentes da velocidade da máquina."""
import sqlite3

import pytest

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.web import consulta_local as query

CNPJ = "16840128000101"
OUTRO_CNPJ = "12345678000199"


def chave(numero, serie=1):
    return f"332611{CNPJ}55{serie:03d}{numero:09d}1000000000"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "manifestacoes.db"
    banco = BancoManifestacoes(str(path))
    banco.fechar()
    with sqlite3.connect(path) as conn:
        conn.executemany("""INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,'210210','Ciência','2026-11-01T10:00:00-03:00',?,?,'teste')""",
            [(CNPJ, chave(n), str(n), str(n)) for n in range(1, 10001)])
    return path


@pytest.mark.parametrize("number,grouped,budget", [
    (None, False, 50000),
    (4321, False, 10000),
    # A contagem agrupada ainda percorre o índice legado; cobrir essa CTE com
    # um índice maior prejudicou a listagem geral no benchmark com CC-e.
    (4321, True, 80000),
])
def test_query_work_is_bounded(database, monkeypatch, number, grouped, budget):
    # Remover o índice de ordenação ou de número deve estourar o orçamento.
    instructions = []
    original = query.abrir_banco

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_progress_handler(lambda: instructions.append(100) or 0, 100)
        return conn

    monkeypatch.setattr(query, "abrir_banco", connect)
    result = query.consultar_eventos(database, CNPJ, query.FiltrosEventos(numero=number),
                                    por_pagina=20, agrupar_cartas=grouped)
    assert result.total == (1 if number else 10000)
    assert [e.numero for e in result.eventos] == ([4321] if number else list(range(10000, 9980, -1)))
    assert sum(instructions) < budget


def test_schema_upgrade_preserves_results_and_is_idempotent(database):
    with sqlite3.connect(database) as conn:
        # Reproduz o esquema anterior, que já tem índice por CNPJ/data.
        conn.execute("DROP INDEX IF EXISTS idx_manifestacoes_consulta_ordem")
        conn.execute("DROP INDEX IF EXISTS idx_manifestacoes_consulta_numero")
    scenarios = [
        (query.FiltrosEventos(), 1, False),
        (query.FiltrosEventos(), 400, False),
        (query.FiltrosEventos(numero=4321, serie=1), 1, True),
        (query.FiltrosEventos(numero=4321, serie=2), 1, False),
        (query.FiltrosEventos(codigo="210200"), 1, False),
    ]
    before = [query.consultar_eventos(database, CNPJ, f, pagina=p, por_pagina=20,
                                     agrupar_cartas=g) for f, p, g in scenarios]
    for _ in range(2):
        banco = BancoManifestacoes(str(database))
        banco.fechar()
    after = [query.consultar_eventos(database, CNPJ, f, pagina=p, por_pagina=20,
                                    agrupar_cartas=g) for f, p, g in scenarios]
    assert after == before
    assert query.consultar_eventos(database, OUTRO_CNPJ, query.FiltrosEventos()).total == 0
    with sqlite3.connect(database) as conn:
        plan = conn.execute("""EXPLAIN QUERY PLAN SELECT id FROM manifestacoes
            WHERE cnpj=? ORDER BY data_evento DESC,CAST(nsu AS INTEGER) DESC,id DESC LIMIT 20""",
            (CNPJ,)).fetchall()
    assert not any("TEMP B-TREE" in row[3] for row in plan)


def test_import_schema_also_upgrades_indexes(database):
    from nfe_consulta.web.xml_store import _schema
    with sqlite3.connect(database) as conn:
        conn.execute("DROP INDEX IF EXISTS idx_manifestacoes_consulta_ordem")
        conn.execute("DROP INDEX IF EXISTS idx_manifestacoes_consulta_numero")
        _schema(conn)
        _schema(conn)
        plan = conn.execute("""EXPLAIN QUERY PLAN SELECT id FROM manifestacoes
            WHERE cnpj=? AND CAST(SUBSTR(chave,26,9) AS INTEGER)=?
            ORDER BY data_evento DESC,CAST(nsu AS INTEGER) DESC,id DESC LIMIT 20""",
            (CNPJ, 4321)).fetchall()
    assert any("<expr>=?" in row[3] for row in plan)
    assert not any("TEMP B-TREE" in row[3] for row in plan)


def test_readonly_query_keeps_legacy_schema(database):
    with sqlite3.connect(database) as conn:
        conn.execute("DROP INDEX idx_manifestacoes_consulta_ordem")
        conn.execute("DROP INDEX idx_manifestacoes_consulta_numero")
        before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    result = query.consultar_eventos(database, CNPJ, query.FiltrosEventos(numero=4321))
    assert result.eventos[0].numero == 4321
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before


def test_encrypted_legacy_database_can_upgrade_indexes(database, tmp_path):
    from nfe_consulta.seguranca_banco import abrir_banco, migrar_banco
    password = "senha sintética para teste"
    encrypted = tmp_path / "protegido.db"
    with sqlite3.connect(database) as conn:
        conn.execute("DROP INDEX idx_manifestacoes_consulta_ordem")
        conn.execute("DROP INDEX idx_manifestacoes_consulta_numero")
    before = query.consultar_eventos(database, CNPJ, query.FiltrosEventos(numero=4321),
                                    agrupar_cartas=True)
    migrar_banco(database, encrypted, password)
    banco = BancoManifestacoes(str(encrypted), password)
    banco.fechar()
    assert query.consultar_eventos(encrypted, CNPJ, query.FiltrosEventos(numero=4321),
                                  password=password, agrupar_cartas=True) == before
    conn = abrir_banco(encrypted, password, somente_leitura=True)
    try:
        plan = conn.execute("""EXPLAIN QUERY PLAN SELECT id FROM manifestacoes
            WHERE cnpj=? ORDER BY data_evento DESC,CAST(nsu AS INTEGER) DESC,id DESC LIMIT 20""",
            (CNPJ,)).fetchall()
        assert not any("TEMP B-TREE" in row[3] for row in plan)
    finally:
        conn.close()
