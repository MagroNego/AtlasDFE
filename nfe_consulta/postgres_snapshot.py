"""Publica uma cópia consistente do histórico local para a Consulta do Atlas."""

import argparse
from contextlib import closing
from dataclasses import asdict
from getpass import getpass
import os
from pathlib import Path
import re

from nfe_consulta.banco import consultar_chave_local
from nfe_consulta.seguranca_banco import abrir_banco, criptografado


def importar(origem: Path, cnpj: str, dsn: str, senha: str | None = None) -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    if not re.fullmatch(r"[0-9]{14}", cnpj):
        raise ValueError("Informe o CNPJ com 14 dígitos.")

    with (
        psycopg.connect(dsn) as destino,
        closing(abrir_banco(origem, senha, somente_leitura=True)) as fonte,
    ):
        destino.execute("SELECT pg_advisory_xact_lock(718320, hashtext(%s))", (cnpj,))
        fonte.execute("BEGIN")
        tabelas = {r[0] for r in fonte.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "manifestacoes" not in tabelas:
            raise ValueError("A origem não é um banco fiscal do Atlas.")
        consultas = [
            f"SELECT {coluna} FROM {tabela} WHERE cnpj=?"
            for tabela, coluna in (
                ("manifestacoes", "chave"),
                ("informacoes_nfe", "chave"),
                ("xml_documentos", "chave"),
                ("cancelamentos_manuais", "chave"),
                ("eventos_cte", "chave_nfe"),
            )
            if tabela in tabelas
        ]
        sql = " UNION ".join(consultas)
        anterior = destino.execute(
            "SELECT 1 FROM consulta_importacoes WHERE cnpj=%s", (cnpj,)
        ).fetchone()
        destino.execute("DELETE FROM consulta_notas WHERE cnpj=%s", (cnpj,))
        total = 0
        with destino.cursor().copy(
            "COPY consulta_notas (cnpj, chave, numero, serie, detalhe) FROM STDIN"
        ) as copy:
            for (chave,) in fonte.execute(sql, (cnpj,) * len(consultas)):
                if not re.fullmatch(r"[0-9]{44}", chave) or chave[20:22] != "55":
                    raise ValueError("A origem contém uma chave de NF-e inválida.")
                nota = consultar_chave_local(fonte, chave, cnpj)
                copy.write_row((
                    cnpj, chave, int(chave[25:34]), int(chave[22:25]),
                    Jsonb({
                        "cancelada": nota.cancelada,
                        "eventos": [asdict(evento) for evento in nota.manifestacoes],
                        "ctes": [asdict(cte) for cte in nota.ctes],
                    }),
                ))
                total += 1
        if not total and anterior:
            raise ValueError("A origem não tem notas deste CNPJ. A cópia anterior foi preservada.")
        destino.execute(
            "INSERT INTO consulta_importacoes (cnpj, importado_em, total) VALUES (%s, now(), %s) "
            "ON CONFLICT (cnpj) DO UPDATE SET importado_em=excluded.importado_em, total=excluded.total",
            (cnpj, total),
        )
        return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origem", type=Path, help="Caminho do SQLite ou SQLCipher fiscal")
    parser.add_argument("--cnpj", required=True)
    args = parser.parse_args()
    dsn = os.environ.get("ATLAS_POSTGRES_DSN")
    if not dsn:
        parser.error("Defina ATLAS_POSTGRES_DSN com a conexão PostgreSQL.")
    senha = getpass("Senha do SQLCipher: ") if criptografado(args.origem) else None
    try:
        total = importar(args.origem, args.cnpj, dsn, senha)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"{total} notas publicadas na Consulta.")


if __name__ == "__main__":
    main()
