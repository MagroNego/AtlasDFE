"""Fila administrativa de consultas consNSU, sem chamadas à SEFAZ pelo formulário."""
from __future__ import annotations

import re

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web.sync_runtime import caminho_banco_configurado

MAX_FILA_ATIVA = 100


def _normalizar_nsu(valor: str) -> str:
    valor = valor.strip()
    if not re.fullmatch(r"[0-9]{1,15}", valor, flags=re.ASCII) or int(valor) == 0:
        raise ValueError("Informe um NSU numérico válido (até 15 dígitos). Não informe número de NF-e.")
    return valor.zfill(15)


def listar_fila(settings, cnpj: str, limite: int = 30) -> list[dict]:
    conexao = abrir_banco(caminho_banco_configurado(settings),
                         settings.current_database_password(), somente_leitura=True)
    try:
        existe = conexao.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fila_nsu_prioridade'"
        ).fetchone()
        if not existe:
            return []
        linhas = conexao.execute("""
            SELECT nsu, prioridade, status, criado_por, criado_em, atualizado_em
            FROM fila_nsu_prioridade WHERE cnpj=?
            ORDER BY CASE WHEN status='pendente' THEN 0 ELSE 1 END,
                     prioridade DESC, atualizado_em DESC LIMIT ?
        """, (cnpj, limite)).fetchall()
        return [dict(nsu=nsu, prioridade=bool(prioridade), status=status,
                     criado_por=autor, criado_em=criado, atualizado_em=atualizado)
                for nsu, prioridade, status, autor, criado, atualizado in linhas]
    finally:
        conexao.close()


def listar_notas_sem_manifestacao(settings, cnpj: str, *, emissao: str = "2026-10-06", limite: int = 100) -> list[dict]:
    """Lista apenas as notas sem evento local, sem pressupor evento existente na SEFAZ."""
    conexao = abrir_banco(caminho_banco_configurado(settings),
                         settings.current_database_password(), somente_leitura=True)
    try:
        linhas = conexao.execute("""
            SELECT d.numero, d.chave, d.emissao
            FROM xml_documentos d
            WHERE d.cnpj=? AND substr(d.emissao,1,10)=?
              AND NOT EXISTS (
                  SELECT 1 FROM manifestacoes m WHERE m.cnpj=d.cnpj AND m.chave=d.chave
              )
            GROUP BY d.chave
            ORDER BY CAST(d.numero AS INTEGER), d.chave
            LIMIT ?
        """, (cnpj, emissao, limite)).fetchall()
        return [dict(numero=numero, chave=chave, emissao=data) for numero, chave, data in linhas]
    finally:
        conexao.close()


def enfileirar_nsu(settings, cnpj: str, valor: str, autor: str, *, alta: bool) -> str:
    nsu = _normalizar_nsu(valor)
    banco = BancoManifestacoes(str(caminho_banco_configurado(settings)),
                               senha=settings.current_database_password())
    try:
        atual, maximo = banco.obter_estado(cnpj)
        if int(maximo) == 0 or int(nsu) > int(maximo):
            raise ValueError("NSU acima do maxNSU conhecido do Atlas; confira o identificador.")
        if banco.pausa_ativa(cnpj):
            raise ValueError("Consultas SEFAZ pausadas. Aguarde a liberação antes de criar novas prioridades.")
        usado = banco.conexao.execute("""
            SELECT 1 FROM consultas_nsu
            WHERE cnpj=? AND nsu=? AND tentado_em>=datetime('now','-1 hour') LIMIT 1
        """, (cnpj, nsu)).fetchone()
        if usado:
            raise ValueError("Este NSU já foi consultado na última hora. Aguarde antes de reenfileirar.")
        if banco.conexao.execute(
            "SELECT 1 FROM documentos_nsu WHERE cnpj=? AND nsu=? LIMIT 1", (cnpj, nsu)
        ).fetchone():
            raise ValueError("NSU já presente no banco. Nova consulta desnecessária.")
        if banco.conexao.execute(
            "SELECT 1 FROM manifestacoes WHERE cnpj=? AND nsu=? LIMIT 1", (cnpj, nsu)
        ).fetchone():
            raise ValueError("NSU de manifestação já registrado no banco.")
        if banco.conexao.execute(
            "SELECT 1 FROM consultas_nsu WHERE cnpj=? AND nsu=? AND resultado IN ('recebido','indisponivel') LIMIT 1",
            (cnpj, nsu)
        ).fetchone():
            raise ValueError("NSU já consultado com resultado definitivo. Não será reenviado.")
        with banco.conexao:
            pendentes = banco.conexao.execute("""
                SELECT COUNT(*) FROM fila_nsu_prioridade
                WHERE cnpj=? AND status IN ('pendente','consultando')
            """, (cnpj,)).fetchone()[0]
            existente = banco.conexao.execute("""
                SELECT status FROM fila_nsu_prioridade WHERE cnpj=? AND nsu=?
            """, (cnpj, nsu)).fetchone()
            if existente and existente[0] == 'consultando':
                raise ValueError("Este NSU está em processamento.")
            if pendentes >= MAX_FILA_ATIVA and (not existente or existente[0] != 'pendente'):
                raise ValueError("Fila cheia. Aguarde o processamento antes de adicionar outros NSUs.")
            banco.conexao.execute("""
                INSERT INTO fila_nsu_prioridade(cnpj,nsu,prioridade,status,criado_por)
                VALUES (?,?,?,'pendente',?)
                ON CONFLICT(cnpj,nsu) DO UPDATE SET
                    prioridade=excluded.prioridade,
                    status='pendente',
                    criado_por=excluded.criado_por,
                    atualizado_em=CURRENT_TIMESTAMP
            """, (cnpj, nsu, int(alta), autor[:120]))
        return nsu
    finally:
        banco.fechar()


def cancelar_nsu(settings, cnpj: str, valor: str) -> bool:
    nsu = _normalizar_nsu(valor)
    banco = BancoManifestacoes(str(caminho_banco_configurado(settings)),
                               senha=settings.current_database_password())
    try:
        with banco.conexao:
            cursor = banco.conexao.execute("""
                UPDATE fila_nsu_prioridade
                SET status='cancelado', atualizado_em=CURRENT_TIMESTAMP
                WHERE cnpj=? AND nsu=? AND status='pendente'
            """, (cnpj, nsu))
        return bool(cursor.rowcount)
    finally:
        banco.fechar()
