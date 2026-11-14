import os
from contextlib import closing

import pytest

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.postgres_snapshot import importar
from nfe_consulta.seguranca_banco import abrir_banco, migrar_banco


CNPJ = "12345678000199"
CHAVE = "33261112345678000199550010000962031000000000"


@pytest.mark.parametrize("criptografado", [False, True])
@pytest.mark.skipif(not os.environ.get("ATLAS_TEST_POSTGRES"), reason="PostgreSQL de teste não configurado")
def test_importacao_atomica_e_repetivel(tmp_path, criptografado):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ["ATLAS_TEST_POSTGRES"]
    assert conninfo_to_dict(dsn).get("dbname", "").startswith("atlas_test_")

    fonte = tmp_path / "origem.db"
    banco = BancoManifestacoes(str(fonte))
    banco.conexao.execute(
        "INSERT INTO manifestacoes (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (CNPJ, CHAVE, "110110", "Carta", "2026-11-13T15:00:00-03:00", "123", "000000000000042", "evento"),
    )
    banco.conexao.execute("INSERT INTO cartas_correcao VALUES (?,?,?,?,?)", (CNPJ, CHAVE, "123", "1", "Texto corrigido"))
    banco.conexao.execute("INSERT INTO informacoes_nfe VALUES (?,?,?,?)", (CNPJ, CHAVE, "Emitente", 1))
    banco.conexao.execute("INSERT INTO estado_distribuicao (cnpj,ult_nsu,max_nsu) VALUES (?,?,?)", (CNPJ, "42", "45"))
    banco.conexao.execute(
        "INSERT INTO eventos_cte "
        "(cnpj,chave_nfe,chave_cte,codigo,data_evento,protocolo,sequencia,transportadora,cnpj_transportadora,nsu,schema_xml) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (CNPJ, CHAVE, "", "610600", "2026-11-13", "456", "1", "Transportadora", "", "", ""),
    )
    banco.conexao.commit()
    banco.fechar()

    senha = "senha-sintetica-atlas" if criptografado else None
    if senha:
        cifrado = tmp_path / "origem_cifrada.db"
        migrar_banco(fonte, cifrado, senha)
        fonte = cifrado
    assert importar(fonte, CNPJ, dsn, senha) == 1
    assert importar(fonte, CNPJ, dsn, senha) == 1
    with psycopg.connect(dsn) as pg:
        numero, serie, detalhe = pg.execute(
            "SELECT numero, serie, detalhe FROM consulta_notas WHERE cnpj=%s AND chave=%s", (CNPJ, CHAVE)
        ).fetchone()
        assert (numero, serie) == (96203, 1)
        assert detalhe["cancelada"] is True
        assert detalhe["ctes"][0]["transportadora"] == "Transportadora"
        assert detalhe["eventos"][0]["correcao"] == "Texto corrigido"
        assert detalhe["eventos"][0]["nsu"] == "000000000000042"
    with closing(abrir_banco(fonte, senha)) as sqlite, sqlite:
        assert sqlite.execute("SELECT ult_nsu, max_nsu FROM estado_distribuicao").fetchone() == ("42", "45")
        sqlite.execute("UPDATE manifestacoes SET chave='inválida'")
    with pytest.raises(ValueError, match="chave"):
        importar(fonte, CNPJ, dsn, senha)
    with psycopg.connect(dsn) as pg:
        assert pg.execute("SELECT count(*) FROM consulta_notas WHERE cnpj=%s", (CNPJ,)).fetchone()[0] == 1
