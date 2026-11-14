import os
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.seguranca_banco import abrir_banco, migrar_banco


CNPJ = "12345678000350"
CHAVE = "33261112345678000350550010000962031000000000"


@pytest.mark.parametrize("criptografado", [False, True])
@pytest.mark.skipif(not all(os.environ.get(k) for k in ("ATLAS_TEST_POSTGRES", "ATLAS_TEST_IMPORT_DLL")), reason="PostgreSQL de teste não configurado")
def test_importacao_atomica_e_repetivel(tmp_path, criptografado):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ["ATLAS_TEST_POSTGRES"]
    app = Path(os.environ["ATLAS_TEST_IMPORT_DLL"])
    pg_config = conninfo_to_dict(dsn)
    assert pg_config.get("dbname", "").startswith("atlas_test_")
    connection = "Host={host};Port={port};Database={dbname};Username={user}".format(**pg_config)
    if "password" in pg_config:
        connection += ";Password=" + pg_config["password"]

    def importar(origem, senha):
        env = dict(os.environ, Atlas__Cnpj=CNPJ, ConnectionStrings__Database=connection)
        if senha:
            env["Atlas__LegacyPassword"] = senha
        else:
            env.pop("Atlas__LegacyPassword", None)
        return subprocess.run(
            ["dotnet", str(app), "--importar-historico", str(origem)],
            env=env, capture_output=True, text=True, check=False, timeout=30,
        )

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
    banco.conexao.execute("INSERT INTO recuperacoes_nsu(cnpj,nsu_local,nsu_sefaz) VALUES (?,?,?)", (CNPJ, "42", "48"))
    banco.conexao.execute(
        "INSERT INTO fila_nsu_prioridade(cnpj,nsu,prioridade,status,criado_por) VALUES (?,?,?,?,?)",
        (CNPJ, "000000000000045", 1, "consultando", "teste"),
    )
    banco.conexao.execute(
        "INSERT INTO consultas_nsu(cnpj,nsu,resposta_xml) VALUES (?,?,?)",
        (CNPJ, "000000000000044", "<resposta>pendente</resposta>"),
    )
    banco.conexao.execute(
        "INSERT INTO documentos_nsu(cnpj,nsu,schema_xml,xml,classificacao) VALUES (?,?,?,?,?)",
        (CNPJ, "000000000000043", "schema", b"<documento />", "ignorado"),
    )
    banco.conexao.commit()
    banco.fechar()

    senha = "senha-sintetica-atlas" if criptografado else None
    if senha:
        cifrado = tmp_path / "origem_cifrada.db"
        migrar_banco(fonte, cifrado, senha)
        fonte = cifrado
    if senha:
        falha = importar(fonte, "senha errada")
        assert falha.returncode != 0
        assert "SQLite/SQLCipher" in falha.stderr
    antes = fonte.read_bytes()
    for _ in range(2):
        resultado = importar(fonte, senha)
        assert resultado.returncode == 0, resultado.stderr
        assert "1 notas" in resultado.stdout
    assert fonte.read_bytes() == antes
    with psycopg.connect(dsn) as pg:
        assert pg.execute("SELECT ult_nsu,max_nsu FROM sefaz_estado WHERE cnpj=%s", (CNPJ,)).fetchone() == (42, 45)
        assert pg.execute("SELECT nsu,alta,status FROM sefaz_fila WHERE cnpj=%s", (CNPJ,)).fetchone() == (45, True, "pendente")
        assert pg.execute("SELECT nsu_enviado,recuperacao,xml FROM sefaz_respostas WHERE cnpj=%s", (CNPJ,)).fetchone() == (44, True, b"<resposta>pendente</resposta>")
        assert pg.execute("SELECT inicio,fim FROM sefaz_lacunas WHERE cnpj=%s", (CNPJ,)).fetchone() == (42, 48)
        assert pg.execute("SELECT nsu FROM sefaz_cobertura WHERE cnpj=%s", (CNPJ,)).fetchone() == (43,)
        assert pg.execute("SELECT nsu FROM sefaz_tentativas WHERE cnpj=%s AND legada", (CNPJ,)).fetchone() == (44,)
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
    resultado = importar(fonte, senha)
    assert resultado.returncode != 0
    assert "chave" in resultado.stderr
    with psycopg.connect(dsn) as pg:
        assert pg.execute("SELECT count(*) FROM consulta_notas WHERE cnpj=%s", (CNPJ,)).fetchone()[0] == 1
