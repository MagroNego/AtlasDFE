"""A paginação enriquece apenas as linhas exibidas, preservando ordem e totais."""
import sqlite3
import pytest
from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.web import consulta_local as query
from nfe_consulta.config import CNPJ_PADRAO


@pytest.mark.parametrize('grouped', [False, True])
def test_metadata_is_loaded_only_for_requested_page(tmp_path, monkeypatch, grouped):
    path = tmp_path / 'notas.db'
    banco = BancoManifestacoes(str(path))
    banco.fechar()
    with sqlite3.connect(path) as conn:
        conn.executemany('''INSERT INTO manifestacoes
            (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
            VALUES (?,?,'210210','Evento','2026-11-01T10:00:00-03:00',?,?,'teste')''',
            [(CNPJ_PADRAO, str(i).zfill(44), str(i), str(i).zfill(15)) for i in range(500)])
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE xml_documentos (cnpj TEXT, chave TEXT)')
        conn.execute('INSERT INTO xml_documentos VALUES (?, ?)', (CNPJ_PADRAO, str(489).zfill(44)))
    opens = []
    calls = []
    original = query.abrir_banco
    def connect(*args, **kwargs):
        opens.append(1)
        conn = original(*args, **kwargs)
        def metadata():
            calls.append(1)
            return 'Emitente'
        conn.create_function('metadata_probe', 0, metadata)
        return conn
    monkeypatch.setattr(query, 'abrir_banco', connect)
    monkeypatch.setattr(query, '_campos_nota', lambda _: 'metadata_probe() AS emitente')
    result = query.consultar_eventos(path, CNPJ_PADRAO, query.normalizar_filtros(),
                                    pagina=2, por_pagina=10, agrupar_cartas=grouped)
    assert result.total == 500
    assert [int(event.nsu) for event in result.eventos] == list(range(489, 479, -1))
    assert len(calls) == 10
    assert len(opens) == 1
    assert result.xml_keys == {str(489).zfill(44)}
