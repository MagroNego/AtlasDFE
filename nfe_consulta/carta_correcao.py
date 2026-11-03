"""Leitura e armazenamento de CC-e registrada e vinculada pela SEFAZ."""
from datetime import datetime
import re

from nfe_consulta.cancelamento import child, text
from nfe_consulta.modelos import Manifestacao

CODIGO = '110110'
DESCRICAO = 'Carta de Correção'
SCHEMA = """
CREATE TABLE IF NOT EXISTS manifestacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cnpj TEXT NOT NULL, chave TEXT NOT NULL,
    codigo TEXT NOT NULL, descricao TEXT NOT NULL, data_evento TEXT NOT NULL,
    protocolo TEXT NOT NULL, nsu TEXT NOT NULL, schema_xml TEXT NOT NULL,
    recebido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cnpj,chave,codigo,protocolo,nsu)
);
CREATE TABLE IF NOT EXISTS cartas_correcao (
    cnpj TEXT NOT NULL, chave TEXT NOT NULL, protocolo TEXT NOT NULL,
    sequencia TEXT NOT NULL, correcao TEXT NOT NULL,
    PRIMARY KEY(cnpj,chave,protocolo)
);
"""


def read_corrections(root, expected_cnpj=None, *, nsu='', schema='', allow_summary=False):
    """Retorna eventos completos; resumos só são aceitos na distribuição."""
    kind = root.tag.rsplit('}', 1)[-1]
    if kind in {'nfeProc', 'retConsSitNFe'}:
        inf = child(child(root, 'NFe'), 'infNFe')
        key = inf.attrib.get('Id', '').removeprefix('NFe') if inf is not None else text(root, 'chNFe')
        results = []
        for candidate in root:
            if candidate.tag.rsplit('}', 1)[-1] != 'procEventoNFe':
                continue
            for item in read_corrections(candidate, expected_cnpj, nsu=nsu, schema=schema):
                if item[0] != key:
                    raise ValueError('Nota e carta de correção divergentes.')
                results.append(item)
        return tuple(results)
    summary = kind == 'resEvento' and allow_summary
    if kind != 'procEventoNFe' and not summary:
        return ()
    event = root if summary else child(child(root, 'evento'), 'infEvento')
    response = root if summary else child(child(root, 'retEvento'), 'infEvento')
    if text(event, 'tpEvento') != CODIGO:
        return ()
    key = text(event, 'chNFe')
    if not re.fullmatch(r'[0-9]{44}', key) or key[20:22] != '55':
        raise ValueError('Chave inválida para CC-e.')
    if expected_cnpj is not None and key[6:20] != expected_cnpj:
        raise ValueError('CC-e de outro emitente.')
    seq = text(event, 'nSeqEvento')
    correction = '' if summary else text(child(event, 'detEvento'), 'xCorrecao')
    if not summary:
        if (text(response, 'cStat') != '135' or text(response, 'tpEvento') != CODIGO
                or text(event, 'CNPJ') != key[6:20]):
            raise ValueError('CC-e sem retorno vinculado da SEFAZ.')
        for field in ('chNFe', 'nSeqEvento', 'tpAmb'):
            if not text(event, field) or text(event, field) != text(response, field):
                raise ValueError('CC-e e retorno divergentes.')
        if text(event, 'tpAmb') != '1' or not 15 <= len(correction) <= 1000:
            raise ValueError('Ambiente ou texto da CC-e inválido.')
    if (seq or not summary) and (not re.fullmatch(r'[0-9]{1,2}', seq) or not 1 <= int(seq) <= 20):
        raise ValueError('Sequência da CC-e inválida.')
    protocol = text(response, 'nProt')
    date = text(response, 'dhRegEvento') or (text(event, 'dhEvento') if summary else '')
    if not re.fullmatch(r'[0-9]{15}', protocol):
        raise ValueError('Protocolo da CC-e inválido.')
    if datetime.fromisoformat(date.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Data da CC-e sem fuso horário.')
    return ((key, Manifestacao(CODIGO, DESCRICAO, date, protocol, nsu, schema, correction, seq)),)


def save_correction(conn, cnpj, key, event):
    """Uma mesma CC-e importada e distribuída é um único evento."""
    conn.execute('''INSERT INTO cartas_correcao(cnpj,chave,protocolo,sequencia,correcao)
        VALUES (?,?,?,?,?) ON CONFLICT(cnpj,chave,protocolo) DO UPDATE SET
        sequencia=CASE WHEN cartas_correcao.sequencia='' THEN excluded.sequencia ELSE cartas_correcao.sequencia END,
        correcao=CASE WHEN cartas_correcao.correcao='' THEN excluded.correcao ELSE cartas_correcao.correcao END''',
        (cnpj, key, event.protocolo, event.sequencia, event.correcao))
    exists = conn.execute('SELECT 1 FROM manifestacoes WHERE cnpj=? AND chave=? AND codigo=? AND protocolo=?',
                          (cnpj, key, CODIGO, event.protocolo)).fetchone()
    if exists:
        if event.nsu:
            conn.execute("UPDATE manifestacoes SET nsu=?,schema_xml=? WHERE cnpj=? AND chave=? AND codigo=? AND protocolo=? AND nsu=''",
                         (event.nsu, event.schema, cnpj, key, CODIGO, event.protocolo))
        return False
    conn.execute('''INSERT INTO manifestacoes(cnpj,chave,codigo,descricao,data_evento,protocolo,nsu,schema_xml)
        VALUES (?,?,?,?,?,?,?,?)''', (cnpj, key, CODIGO, DESCRICAO, event.data, event.protocolo, event.nsu, event.schema))
    return True
