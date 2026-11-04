"""Compara a Emissão antes/depois em processos e bancos temporários separados.

Na raiz: python -m scripts.benchmark_emissao_response --size 10000 --repeats 3
O padrão mede SQLCipher. --engine plain mede SQLite sem criptografia.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
from statistics import median
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory, TemporaryFile
from time import perf_counter

BASELINE = 'd872efa7ef1340ddd425d1e36837cf0152abde91'


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('Use um inteiro positivo.')
    return result


@contextmanager
def count_opens():
    from nfe_consulta import seguranca_banco
    original = seguranca_banco.abrir_banco
    durations = []
    aliases = []

    def measured(*args, **kwargs):
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            durations.append((perf_counter() - started) * 1000)

    for name, module in tuple(sys.modules.items()):
        if name.startswith('nfe_consulta.') and getattr(module, 'abrir_banco', None) is original:
            aliases.append(module)
            module.abrir_banco = measured
    try:
        yield durations
    finally:
        for module in aliases:
            module.abrir_banco = original


def add_item(database, number):
    from scripts.benchmark_queries import CNPJ, key
    from nfe_consulta.xml_reader import FIELDS_ITENS
    row = dict.fromkeys(FIELDS_ITENS, '')
    row.update({'Chave Acesso': key(number), 'Numero Nota': str(number),
        'Serie': str(1 + number % 3), 'Data Emissao': f'2026-11-{1 + number % 28:02d}',
        'Item': '1', 'Codigo': 'BENCH-ITEM', 'Descricao': 'Item sintético de desempenho',
        'NCM': '87089990', 'CFOP': '6101', 'Unidade': 'UN', 'Qtd': '1',
        'Vl. Unitario': '100.00', 'Vl. Total': '100.00'})
    with sqlite3.connect(database) as conn:
        conn.execute('''INSERT INTO xml_relatorio(cnpj,chave,tipo,linha,dados,busca)
            VALUES (?,?,'itens',1,?,?)''',
            (CNPJ, key(number), json.dumps(row, ensure_ascii=False, sort_keys=True), 'bench-item item sintético'))


async def measure(webapp, settings, size, number, repeats):
    import httpx
    from scripts.benchmark_queries import key
    from scripts.benchmark_web_response import close_audit
    routes = [('/xml?tipo=notas', 'lista'), ('/xml?tipo=notas&pagina=2', 'pagina_2'),
              (f'/xml/itens/{key(number)}', 'itens'),
              (f'/xml/manifestacoes/{key(number)}', 'manifestacoes')]
    app = webapp.create_app(settings)
    records = []
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://benchmark.local') as client:
                with count_opens() as openings:
                    for simultaneous in (1, 4):
                        for route, label in routes:
                            expected = None

                            async def request():
                                nonlocal expected
                                started = perf_counter()
                                response = await client.get(route)
                                duration = (perf_counter() - started) * 1000
                                if response.status_code != 200 or 'alert error' in response.text or '<tbody>' not in response.text:
                                    raise RuntimeError(f'Resposta inválida em {route}: {response.status_code}')
                                if label.startswith('lista') or label == 'pagina_2':
                                    if f'{size} registro' not in response.text:
                                        raise RuntimeError(f'Total inesperado em {route}')
                                if label == 'itens' and 'BENCH-ITEM' not in response.text:
                                    raise RuntimeError('Item sintético não foi exibido.')
                                if label == 'manifestacoes' and 'Ciência da Operação' not in response.text:
                                    raise RuntimeError('Manifestação sintética não foi exibida.')
                                digest = hashlib.sha256(response.content).hexdigest()
                                if expected is None:
                                    expected = digest
                                if digest != expected:
                                    raise RuntimeError(f'HTML mudou entre amostras de {route}')
                                return duration

                            await request()  # Aquecimento fora das amostras.
                            openings.clear()
                            values = []
                            for _ in range(repeats):
                                values.extend(await asyncio.gather(*(request() for _ in range(simultaneous))))
                            records.append(dict(caso=label, rota=route, simultaneas=simultaneous,
                                grupos=repeats, requisicoes=len(values), mediana_ms=round(median(values), 3),
                                amostras_ms=[round(v, 3) for v in values], html_sha256=expected,
                                aberturas=len(openings), aberturas_por_requisicao=len(openings) / len(values),
                                abertura_mediana_ms=round(median(openings), 3) if openings else None))
    finally:
        close_audit(app)
    return records


def worker(args):
    from scripts.benchmark_web_response import fixture, settings_for, load_webapp, engine_metadata
    from nfe_consulta.seguranca_banco import migrar_banco
    with TemporaryDirectory(prefix='atlas-emissao-fixture-') as temporary:
        root = Path(temporary)
        database = fixture(root, args.size)
        number = min(10, args.size)
        add_item(database, number)
        password = None
        if args.engine == 'cipher':
            password = 'senha sintética de benchmark'
            target = database.with_name('encrypted.db')
            migrar_banco(database, target, password)
            database = target
        settings = settings_for(root, database, password)
        webapp = load_webapp(settings)
        sources = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                   for name in ('nfe_consulta/web/xml_routes.py', 'nfe_consulta/web/xml_store.py',
                                'nfe_consulta/web/consulta_local.py', 'nfe_consulta/web/emissao_data.py')
                   if Path(name).is_file()}
        measurements = asyncio.run(measure(webapp, settings, args.size, number, args.repeats))
        for name, digest in sources.items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f'Fonte alterada durante a medição: {name}')
        return dict(python=platform.python_version(), sistema=platform.platform(), notas=args.size,
                    banco='SQLCipher' if password else 'SQLite sem criptografia',
                    motor=engine_metadata(database, password), fontes_sha256=sources, respostas=measurements)


def archive(commit, destination):
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', commit):
        raise ValueError('A referência deve ser o hash de um commit.')
    with TemporaryFile() as stream:
        subprocess.run(['git', 'archive', commit], stdout=stream, check=True)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as tar:
            tar.extractall(destination, filter='data')
    shutil.copyfile(__file__, destination / 'scripts' / Path(__file__).name)


def run_child(directory, args):
    environment = dict(os.environ, PYTHONPATH=str(directory))
    process = subprocess.run([sys.executable, '-m', 'scripts.benchmark_emissao_response', '--worker',
        '--size', str(args.size), '--repeats', str(args.repeats), '--engine', args.engine],
        cwd=directory, env=environment, check=True, capture_output=True, text=True)
    return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default=BASELINE)
    parser.add_argument('--size', type=positive, default=10000)
    parser.add_argument('--repeats', type=positive, default=3)
    parser.add_argument('--engine', choices=('plain', 'cipher'), default='cipher')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args), ensure_ascii=False, indent=2))
        return
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix='atlas-emissao-baseline-') as temporary:
        destination = Path(temporary)
        archive(args.baseline_ref, destination)
        before = run_child(destination, args)
        after = run_child(repository, args)
    for old, new in zip(before['respostas'], after['respostas'], strict=True):
        if (old['caso'], old['simultaneas'], old['html_sha256']) != (new['caso'], new['simultaneas'], new['html_sha256']):
            raise RuntimeError(f'HTML divergente entre versões: {old["caso"]}')
    print(json.dumps(dict(baseline=args.baseline_ref, transporte='ASGI em processo: sem TCP, parser HTTP, rede, assets, JavaScript ou pintura no navegador',
        comparacao='Blocos sequenciais em processos separados; comparação exploratória, sem p95.',
        fixture='Mesmas regras de geração; uma ciência por nota e CC-e em 10%; item real na nota min(10, tamanho).',
        antes=before, depois=after), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
