"""Compara índices em bancos temporários; nunca abre um banco do usuário.

Na raiz do repositório: python -m scripts.benchmark_queries --sizes 10000 100000
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sqlite3
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.indices_consulta import criar_indices_manifestacoes
from nfe_consulta.web.consulta_local import FiltrosEventos, consultar_eventos

CNPJ = "16840128000101"
NEW_INDEXES = ("idx_manifestacoes_consulta_ordem", "idx_manifestacoes_consulta_numero")


def key(number: int) -> str:
    return f"332611{CNPJ}55{1 + number % 3:03d}{number:09d}1000000000"


def rows(start: int, stop: int):
    for n in range(start, stop):
        yield (CNPJ, key(n), "210210", "Ciência", f"2026-11-{1 + n % 28:02d}T10:00:00-03:00",
               str(n), str(n).zfill(15), "teste")


INSERT = """INSERT INTO manifestacoes
    (cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
    VALUES (?,?,?,?,?,?,?,?)"""


def seed(path: Path, size: int) -> None:
    banco = BancoManifestacoes(str(path))
    banco.fechar()
    with sqlite3.connect(path) as conn:
        for name in NEW_INDEXES:
            conn.execute(f"DROP INDEX {name}")
        conn.executemany(INSERT, rows(1, size + 1))
        conn.executemany("""INSERT INTO informacoes_nfe(cnpj,chave,emitente)
            VALUES (?,?,'Emitente de teste')""", ((CNPJ, key(n)) for n in range(10, size + 1, 10)))
        conn.executemany(INSERT,
            ((CNPJ, key(n), "110110", "Carta de Correção", "2026-11-28T12:00:00-03:00",
              f"c{n}", str(size + n).zfill(15), "teste") for n in range(10, size + 1, 10)))
        conn.executemany("""INSERT INTO cartas_correcao(cnpj,chave,protocolo,sequencia,correcao)
            VALUES (?,?,?,'1','Texto sintético da correção')""",
            ((CNPJ, key(n), f"c{n}") for n in range(10, size + 1, 10)))


def scenarios(size: int):
    for grouped in (False, True):
        for name, filters, page in (
            ("primeira", FiltrosEventos(), 1),
            ("profunda", FiltrosEventos(), max(1, size // 40)),
            ("numero", FiltrosEventos(numero=size // 2), 1),
            ("periodo_serie", FiltrosEventos(data_inicial=date(2026, 11, 10),
                                           data_final=date(2026, 11, 20), serie=2), 1),
        ):
            yield name, filters, page, grouped


def run(size: int, repeats: int, directory: Path) -> dict:
    original = directory / "original.db"
    seed(original, size)
    paths = {}
    results = {}
    expected = {}
    for mode in ("anterior", "indices"):
        path = directory / f"{mode}.db"
        shutil.copyfile(original, path)
        started = perf_counter()
        if mode == "indices":
            with sqlite3.connect(path) as conn:
                criar_indices_manifestacoes(conn)
        results[mode] = dict(modo=mode, migracao_ms=round((perf_counter() - started) * 1000, 3),
                             bytes=path.stat().st_size, consultas=[])
        paths[mode] = path
    for name, filters, page, grouped in scenarios(size):
        timings = {mode: [] for mode in paths}
        for iteration in range(repeats):
            # Alternar a ordem reduz o viés de aquecimento e da carga do host.
            modes = tuple(paths) if iteration % 2 == 0 else tuple(reversed(paths))
            for mode in modes:
                started = perf_counter()
                result = consultar_eventos(paths[mode], CNPJ, filters, pagina=page,
                                            por_pagina=20, agrupar_cartas=grouped)
                timings[mode].append((perf_counter() - started) * 1000)
                digest = hashlib.sha256(repr(asdict(result)).encode()).hexdigest()
                identity = (name, grouped)
                expected.setdefault(identity, digest)
                if digest != expected[identity]:
                    raise RuntimeError(f"Resultados divergentes em {mode}/{identity}")
        for mode in paths:
            results[mode]["consultas"].append(dict(caso=name, agrupado=grouped,
                mediana_ms=round(median(timings[mode]), 3),
                amostras_ms=[round(t, 3) for t in timings[mode]], total=result.total))
    for mode, path in paths.items():
        timings = []
        # Mesmo lote em cada cópia; inclui commit e manutenção dos índices.
        for iteration in range(repeats):
            start = size + 1 + iteration * 1000
            started = perf_counter()
            with sqlite3.connect(path) as conn:
                conn.executemany(INSERT, rows(start, start + 1000))
            timings.append((perf_counter() - started) * 1000)
        results[mode]["inserir_1000_ms"] = round(median(timings), 3)
        results[mode]["insercao_amostras_ms"] = [round(t, 3) for t in timings]
    return dict(notas=size, eventos=size + size // 10, resultados=list(results.values()))


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Use um inteiro positivo.")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=positive, default=[10000, 100000])
    parser.add_argument("--repeats", type=positive, default=5)
    args = parser.parse_args()
    report = dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version,
                  sistema=platform.platform(), banco="SQLite sem criptografia", por_pagina=20,
                  repeticoes=args.repeats, bases=[])
    for size in args.sizes:
        with TemporaryDirectory(prefix="atlas-benchmark-") as directory:
            report["bases"].append(run(size, args.repeats, Path(directory)))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
