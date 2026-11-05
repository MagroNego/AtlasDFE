"""Mede respostas HTML completas em ASGI, sem abrir dados ou serviços externos.

Na raiz: python -m scripts.benchmark_web_response --sizes 10000 100000
Adicione --encrypted para comparar SQLCipher; --serve 8765 abre só uma fixture
temporária em 127.0.0.1 para teste de navegador, até interromper o processo.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from functools import wraps
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sqlite3
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.indices_consulta import criar_indices_manifestacoes
from nfe_consulta.seguranca_banco import abrir_banco, migrar_banco
from nfe_consulta.web.settings import WebSettings
from scripts.benchmark_queries import CNPJ, key, positive, seed


def settings_for(root: Path, database: Path, password: str | None = None) -> WebSettings:
    return WebSettings(environment='development', auth_mode='dev',
        user_header='X-NFE-User', name_header='X-NFE-Name',
        proxy_secret_header='X-NFE-Proxy-Secret', proxy_secret=None,
        admin_users=frozenset(), dev_user='benchmark', dev_name='Benchmark', dev_admin=True,
        csrf_secret='benchmark-' * 8, database_path=database, database_password=password,
        certificate_store='CurrentUser', certificate_thumbprint=None,
        bind_host='127.0.0.1', bind_port=8765, root_path='', forwarded_allow_ips='127.0.0.1',
        audit_log=root / 'logs' / 'audit.log', local_auth_enabled=False,
        corporate_auth_enabled=False, admin_accounts_path=root / 'accounts.db',
        certificate_path_file=root / 'secrets' / 'cert-path.txt',
        certificate_password_file=root / 'secrets' / 'cert-password.txt',
        database_path_file=root / 'secrets' / 'db-path.txt',
        database_password_file=root / 'secrets' / 'db-password.txt',
        email_recipients_file=root / 'secrets' / 'email.json',
        smtp_password_file=root / 'secrets' / 'smtp.txt',
        auto_sync_config_path=root / 'secrets' / 'schedule.json',
        auto_sync_enabled=False, recovery_enabled=False, smtp_host='')


def load_webapp(settings):
    # web.app constructs a default app at import time. Give that construction
    # the same isolated configuration, rather than reading installation secrets.
    from nfe_consulta.web import settings as settings_module
    original = settings_module.get_settings
    already_loaded = 'nfe_consulta.web.app' in sys.modules
    settings_module.get_settings = lambda: settings
    try:
        module = importlib.import_module('nfe_consulta.web.app')
    finally:
        settings_module.get_settings = original
    module.get_settings = original
    if not already_loaded:
        close_audit(module.app)
    return module


def close_audit(app):
    """Fecha apenas os handlers dos apps criados por este benchmark."""
    logger = app.state.audit._logger
    for handler in tuple(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def engine_metadata(database, password):
    conn = abrir_banco(database, password, somente_leitura=True)
    try:
        result = dict(sqlite=conn.execute('SELECT sqlite_version()').fetchone()[0])
        if password is not None:
            from importlib.metadata import packages_distributions, version
            distributions = packages_distributions().get('sqlcipher3', [])
            result['drivers'] = {name: version(name) for name in distributions}
            for name in ('cipher_version', 'kdf_iter', 'cipher_kdf_algorithm', 'cipher_hmac_algorithm'):
                row = conn.execute('PRAGMA ' + name).fetchone()
                result[name] = row[0] if row else None
        return result
    finally:
        conn.close()


def fixture(root: Path, size: int) -> Path:
    if CNPJ != CNPJ_PADRAO:
        raise RuntimeError('O CNPJ do gerador diverge da configuração do aplicativo.')
    database = root / 'dados' / 'benchmark.db'
    database.parent.mkdir()
    seed(database, size)
    from nfe_consulta.web.xml_store import _schema
    with sqlite3.connect(database) as conn:
        criar_indices_manifestacoes(conn)
        _schema(conn)
        conn.execute('''INSERT INTO estado_distribuicao(cnpj,ult_nsu,max_nsu,atualizado_em)
            VALUES (?,'1','1','2026-11-04 03:00:00')''', (CNPJ,))
        conn.executemany('''INSERT INTO xml_documentos
            (cnpj,chave,numero,serie,emissao,emitente,destinatario,valor,itens,nome,sha256,xml)
            VALUES (?,?,?,?,?,'Emitente sintético','Destinatário sintético','100.00',1,'teste.xml',?,?)''',
            ((CNPJ, key(n), str(n), str(1 + n % 3), f'2026-11-{1 + n % 28:02d}', '0' * 64, b'')
             for n in range(1, size + 1)))
    return database


@contextmanager
def profile_stages(webapp):
    from nfe_consulta.web import xml_store
    stages = {}
    originals = []
    targets = [(webapp, '_consulta_database_state'), (webapp, 'consultar_eventos'),
               (webapp, '_render'), (xml_store, 'query_report'),
               (webapp, 'consultar_pagina')]
    shared = sys.modules.get('nfe_consulta.web.consulta_data')
    if shared is not None:
        targets.extend((shared, name) for name in ('estado_consulta', 'consultar_eventos_na_conexao'))
    for module, name in targets:
        if not hasattr(module, name):
            continue
        original = getattr(module, name)
        @wraps(original)
        def timed(*args, _function=original, _name=name, **kwargs):
            started = perf_counter()
            try:
                return _function(*args, **kwargs)
            finally:
                stages.setdefault(_name, []).append((perf_counter() - started) * 1000)
        originals.append((module, name, original))
        setattr(module, name, timed)
    try:
        yield stages
    finally:
        for module, name, original in originals:
            setattr(module, name, original)


async def measure(webapp, settings, size, repeats, simultaneous):
    results = []
    with profile_stages(webapp) as stages:
        app = webapp.create_app(settings)
        transport = httpx.ASGITransport(app=app)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=transport, base_url='http://benchmark.local') as client:
                    for route in ('/consulta?consultar=1', '/consulta?consultar=1&pagina=2',
                                  '/xml?tipo=notas', '/xml?tipo=notas&pagina=2'):
                        async def request():
                            started = perf_counter()
                            response = await client.get(route)
                            elapsed = (perf_counter() - started) * 1000
                            if response.status_code != 200 or 'alert error' in response.text:
                                raise RuntimeError(f'Resposta inválida em {route}: {response.status_code}')
                            if f'{size} registro' not in response.text or '<tbody>' not in response.text:
                                raise RuntimeError(f'Consulta vazia/incompleta em {route}')
                            return elapsed, len(response.content)
                        await request()  # Aquecimento fora das amostras.
                        stages.clear()
                        samples = []
                        for _ in range(repeats):
                            samples.extend(await asyncio.gather(*(request() for _ in range(simultaneous))))
                        results.append(dict(rota=route, simultaneas=simultaneous,
                            grupos=repeats, requisicoes=len(samples),
                            mediana_ms=round(median(t for t, _ in samples), 3),
                            amostras_ms=[round(t, 3) for t, _ in samples], bytes_html=samples[-1][1],
                            etapas={name: dict(chamadas=len(values), mediana_ms=round(median(values), 3))
                                    for name, values in stages.items()}))
        finally:
            close_audit(app)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', nargs='+', type=positive, default=[10000, 100000])
    parser.add_argument('--repeats', type=positive, default=5)
    parser.add_argument('--encrypted', action='store_true')
    parser.add_argument('--serve', type=positive)
    parser.add_argument('--browser-blocker', default='Não medido por este benchmark ASGI.')
    args = parser.parse_args()
    if args.serve and args.serve > 65535:
        parser.error('--serve deve ser uma porta até 65535.')
    report = dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version,
        sistema=platform.platform(), repeticoes=args.repeats,
        transporte='ASGI in-process: middleware + banco + templates + corpo HTML; sem TCP, parser HTTP, rede ou pintura no navegador',
        navegador=dict(medido=False, motivo=args.browser_blocker),
        bases=[])
    for size in args.sizes:
        with TemporaryDirectory(prefix='atlas-web-benchmark-') as temporary:
            root = Path(temporary)
            database = fixture(root, size)
            cfg = settings_for(root, database)
            webapp = load_webapp(cfg)
            report['app_sha256'] = hashlib.sha256(Path(webapp.__file__).read_bytes()).hexdigest()
            source_root = Path(webapp.__file__).parents[1]
            report['fontes_sha256'] = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                for name in ('web/app.py', 'web/consulta_local.py', 'web/consulta_data.py')
                if (source_root / name).is_file()}
            for encrypted in ((False, True) if args.encrypted else (False,)):
                password = None
                current = database
                if encrypted:
                    password = 'senha sintética de benchmark'
                    current = root / 'dados' / 'encrypted.db'
                    migrar_banco(database, current, password)
                cfg = settings_for(root, current, password)
                if args.serve:
                    import uvicorn
                    app = webapp.create_app(cfg)
                    try:
                        uvicorn.run(app, host='127.0.0.1', port=args.serve)
                    finally:
                        close_audit(app)
                    return
                measurements = []
                for simultaneous in (1, 4):
                    measurements.extend(asyncio.run(measure(webapp, cfg, size, args.repeats, simultaneous)))
                report['bases'].append(dict(notas=size, eventos=size + size // 10,
                    banco='SQLCipher' if encrypted else 'SQLite sem criptografia',
                    motor=engine_metadata(current, password), respostas=measurements))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
