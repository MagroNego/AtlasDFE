"""Compara o código do PR #15 e a contagem atual em bancos temporários idênticos.

Execute na raiz: python -m scripts.benchmark_grouped_count > benchmark-contagem.json
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import re
import sqlite3
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from types import ModuleType

from nfe_consulta.indices_consulta import criar_indices_manifestacoes
from nfe_consulta.web import consulta_local
from scripts.benchmark_queries import CNPJ, positive, scenarios, seed

BASELINE = '6a1ff061f10269bf23c5e3af8237dea363aa7e9f'


def baseline_module(commit):
    if not re.fullmatch(r'[a-fA-F0-9]{7,40}', commit):
        raise ValueError('Informe o hash de um commit local.')
    source = subprocess.check_output(
        ['git', 'show', f'{commit}:nfe_consulta/web/consulta_local.py'], text=True)
    module = ModuleType('atlas_benchmark_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, f'{commit}:consulta_local.py', 'exec'), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default=BASELINE)
    parser.add_argument('--sizes', nargs='+', type=positive, default=[10000, 100000])
    parser.add_argument('--repeats', type=positive, default=9)
    args = parser.parse_args()
    previous = baseline_module(args.baseline_ref)
    versions = {'pr15': previous, 'contagem_nova': consulta_local}
    report = dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version,
                  sistema=platform.platform(), baseline=args.baseline_ref,
                  banco='SQLite sem criptografia', repeticoes=args.repeats, por_pagina=20,
                  consulta_sha256=hashlib.sha256(Path(consulta_local.__file__).read_bytes()).hexdigest(),
                  bases=[])
    for size in args.sizes:
        with TemporaryDirectory(prefix='atlas-contagem-') as directory:
            path = Path(directory) / 'benchmark.db'
            seed(path, size)
            with sqlite3.connect(path) as conn:
                criar_indices_manifestacoes(conn)
            results = []
            for name, filters, page, grouped in scenarios(size):
                if not grouped:
                    continue
                timings = {name: [] for name in versions}
                expected = None
                for iteration in range(args.repeats):
                    modes = tuple(versions) if iteration % 2 == 0 else tuple(reversed(versions))
                    for mode in modes:
                        started = perf_counter()
                        result = versions[mode].consultar_eventos(path, CNPJ, filters,
                            pagina=page, por_pagina=20, agrupar_cartas=True)
                        timings[mode].append((perf_counter() - started) * 1000)
                        fingerprint = hashlib.sha256(repr(asdict(result)).encode()).hexdigest()
                        if expected is None:
                            expected = fingerprint
                        if fingerprint != expected:
                            raise RuntimeError(f'Resultado divergente em {size}/{name}/{mode}')
                results.append(dict(caso=name, pagina=page, total=result.total,
                    versoes={mode: dict(mediana_ms=round(median(values), 3),
                                       amostras_ms=[round(t, 3) for t in values])
                             for mode, values in timings.items()}))
            report['bases'].append(dict(notas=size, eventos=size + size // 10, consultas=results))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
