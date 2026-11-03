"""CC-e importada junto às notas, sem alterar dados fiscais ou cursor."""
from pathlib import Path
import runpy

import pytest
from openpyxl import load_workbook

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.modelos import RetornoDistribuicao
from nfe_consulta.parser_distribuicao import parse_documento_distribuido
from nfe_consulta.relatorio_eventos import gravar_eventos_xlsx
from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web import xml_store
from nfe_consulta.web.consulta_local import consultar_eventos, normalizar_filtros

h = runpy.run_path(str(Path(__file__).with_name('test_cancelamento_xml.py')))
KEY, web, batch = h['KEY'], h['web'], h['batch']
CORRECAO = 'Corrigir o endereço de entrega para Rua das Flores, 100.'


def cce(seq=1, status='135', correction=CORRECAO):
    return h['cancellation'](status).replace(b'110111', b'110110').replace(
        b'<nSeqEvento>1</nSeqEvento>', f'<nSeqEvento>{seq}</nSeqEvento>'.encode()).replace(
        b'<descEvento>Cancelamento</descEvento>',
        f'<descEvento>Carta de Correcao</descEvento><xCorrecao>{correction}</xCorrecao>'.encode()).replace(
        b'135260000000002', f'1352600000000{seq:02d}'.encode())


@pytest.mark.parametrize('event_first', [True, False])
def test_cce_mixed_batch_query_export_and_idempotence(tmp_path, event_first):
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    payloads = [cce(), h['helpers']['xml'](), cce(2)]
    if not event_first:
        payloads.reverse()
    result = batch(cfg, tmp_path, payloads)
    assert result['cartas_correcao'] == 2
    assert result['importadas'] == 1 and result['erros'] == result['cancelamentos'] == 0
    assert batch(cfg, tmp_path, payloads)['duplicadas'] == 3
    filtros = normalizar_filtros(chave=KEY, codigo='110110')
    events = consultar_eventos(cfg.database_path, CNPJ_PADRAO, filtros).eventos
    assert len(events) == 2
    assert {e.sequencia for e in events} == {'1', '2'}
    assert all(e.correcao == CORRECAO and e.situacao == 'Autorizada' for e in events)
    assert xml_store.query_report(cfg.database_path, CNPJ_PADRAO)['rows'][0]['Situação'] == 'Autorizada'
    dest = tmp_path / 'eventos.xlsx'
    gravar_eventos_xlsx(dest, events, filtros)
    ws = load_workbook(dest).active
    assert ws.cell(2, 14).value == CORRECAO
    conn = abrir_banco(cfg.database_path, somente_leitura=True)
    assert conn.execute('SELECT COUNT(*) FROM estado_distribuicao').fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize('payload', [
    cce(status='136'), cce(status='204'), cce(seq=21), cce(correction='Curta'),
    cce().replace(b'<tpAmb>1</tpAmb>', b'<tpAmb>2</tpAmb>'),
    cce().replace(b'<nSeqEvento>1</nSeqEvento>', b'<nSeqEvento>2</nSeqEvento>', 1),
    cce().replace(CNPJ_PADRAO.encode(), b'00000000000000', 1),
    cce().replace(b'<retEvento', b'<semRetorno').replace(b'</retEvento>', b'</semRetorno>'),
    b'<!DOCTYPE procEventoNFe>' + cce(),
])
def test_invalid_cce_isolated_from_valid_note(tmp_path, payload):
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    result = batch(cfg, tmp_path, [payload, h['helpers']['xml']()])
    assert result['erros'] == result['importadas'] == 1
    assert result['cartas_correcao'] == 0


def test_distribution_deduplicates_import_and_preserves_cancelled_status(tmp_path):
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    batch(cfg, tmp_path, [h['cancellation'](), cce()])
    event = parse_documento_distribuido(cce(), '000000000000001', 'procEventoNFe')
    assert event is not None and event[1].correcao == CORRECAO
    banco = BancoManifestacoes(str(cfg.database_path))
    assert banco.salvar_manifestacoes(CNPJ_PADRAO, RetornoDistribuicao(138, 'ok', '1', '1', (event,))) == 0
    banco.fechar()
    events = consultar_eventos(cfg.database_path, CNPJ_PADRAO, normalizar_filtros(chave=KEY)).eventos
    assert len(events) == 1 and events[0].cancelada


def test_summary_enriched_by_import_and_full_consultation(tmp_path):
    from nfe_consulta.parser import parse_retorno_consulta
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    summary = f'''<resEvento xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>{KEY}</chNFe>
    <tpEvento>110110</tpEvento><dhEvento>2026-10-07T09:00:00-03:00</dhEvento>
    <nProt>135260000000001</nProt></resEvento>'''.encode()
    event = parse_documento_distribuido(summary, '000000000000001', 'resEvento')
    banco = BancoManifestacoes(str(cfg.database_path))
    assert banco.salvar_retorno(CNPJ_PADRAO, RetornoDistribuicao(138, 'ok', '1', '1', (event,))) == 1
    banco.fechar()
    assert batch(cfg, tmp_path, [cce()])['duplicadas'] == 1
    events = consultar_eventos(cfg.database_path, CNPJ_PADRAO, normalizar_filtros(chave=KEY)).eventos
    assert len(events) == 1 and events[0].correcao == CORRECAO and events[0].sequencia == '1'
    assert events[0].nsu == '000000000000001'
    banco = BancoManifestacoes(str(cfg.database_path))
    assert banco.consultar_chave(KEY, CNPJ_PADRAO).manifestacoes[0].correcao == CORRECAO
    banco.fechar()
    root = b'<retConsSitNFe xmlns="http://www.portalfiscal.inf.br/nfe"><cStat>100</cStat>' + cce() + b'</retConsSitNFe>'
    assert parse_retorno_consulta(root, KEY).manifestacoes[0].correcao == CORRECAO
    assert not parse_retorno_consulta(root.replace(b'<cStat>135', b'<cStat>136'), KEY).manifestacoes


def test_cce_attached_to_duplicate_note_and_zip(tmp_path):
    from zipfile import ZipFile
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    batch(cfg, tmp_path, [h['helpers']['xml']()])
    note = h['helpers']['xml']()
    # Container completo com NF-e e evento processado, como no retorno de consulta.
    combined = note.replace(b'</nfeProc>', cce() + b'</nfeProc>')
    result = batch(cfg, tmp_path, [combined])
    assert result['cartas_correcao'] == result['duplicadas'] == 1 and result['erros'] == 0
    archive = tmp_path / 'notas.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('notas/nota.xml', note)
        z.writestr('cartas/carta.xml', cce(2))
    result = xml_store.import_batch(cfg.database_path, CNPJ_PADRAO, [(archive, archive.name)])
    assert result['cartas_correcao'] == result['duplicadas'] == 1 and result['erros'] == 0


def test_correction_text_escaped_in_page_and_excel(tmp_path):
    from conftest import authenticated_user
    from fastapi.testclient import TestClient
    from nfe_consulta.web.app import create_app
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    text = '=HYPERLINK("https://example.invalid") &lt;script&gt;alert(1)&lt;/script&gt;'
    assert batch(cfg, tmp_path, [cce(correction=text)])['cartas_correcao'] == 1
    with TestClient(create_app(cfg)) as client:
        authenticated_user(client)
        page = client.get('/consulta?data_inicial=2026-10-01&data_final=2026-10-31&chave=' + KEY)
        assert page.status_code == 200
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page.text
        assert '<script>alert(1)</script>' not in page.text
    filtros = normalizar_filtros(chave=KEY)
    events = consultar_eventos(cfg.database_path, CNPJ_PADRAO, filtros).eventos
    dest = tmp_path / 'safe.xlsx'
    gravar_eventos_xlsx(dest, events, filtros)
    cell = load_workbook(dest).active.cell(2, 14)
    assert cell.data_type == 's' and cell.value.startswith('=HYPERLINK')


def test_consultation_wrapper_imports_cancellation_and_correction(tmp_path):
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    wrapper = f'<retConsSitNFe xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>{KEY}</chNFe><cStat>101</cStat>'.encode()
    wrapper += cce() + h['cancellation']() + b'</retConsSitNFe>'
    result = batch(cfg, tmp_path, [h['helpers']['xml'](), wrapper])
    assert result['cartas_correcao'] == result['cancelamentos'] == result['importadas'] == 1
    assert result['erros'] == 0
    assert xml_store.query_report(cfg.database_path, CNPJ_PADRAO)['rows'][0]['Situação'] == 'Cancelada'


def test_correction_attached_to_note_row_with_pagination_and_filters(tmp_path):
    from nfe_consulta.modelos import Manifestacao
    from nfe_consulta.web.consulta_local import consultar_eventos_exportacao
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    batch(cfg, tmp_path, [cce(), cce(2)])
    banco = BancoManifestacoes(str(cfg.database_path))
    banco.salvar_manifestacoes(CNPJ_PADRAO, RetornoDistribuicao(138, 'ok', '1', '1', ((KEY,
        Manifestacao('210200', 'Confirmação da Operação', '2026-10-19T15:30:55-03:00', '135260000000099')),)))
    banco.fechar()
    filtros = normalizar_filtros(chave=KEY)
    result = consultar_eventos(cfg.database_path, CNPJ_PADRAO, filtros, agrupar_cartas=True, por_pagina=1)
    assert result.total == 1 and result.paginas == 1
    assert result.eventos[0].codigo == '210200'
    assert [c.sequencia for c in result.eventos[0].cartas_correcao] == ['2', '1']
    assert result.eventos[0].cartas_correcao[0].correcao == CORRECAO
    assert len(consultar_eventos_exportacao(cfg.database_path, CNPJ_PADRAO, filtros)) == 3
    # O filtro de CC-e ainda encontra a nota, sem uma linha chamada Carta de Correção.
    filtered = consultar_eventos(cfg.database_path, CNPJ_PADRAO,
        normalizar_filtros(chave=KEY, codigo='110110'), agrupar_cartas=True)
    assert filtered.total == 1 and filtered.eventos[0].codigo != '110110'
    assert len(filtered.eventos[0].cartas_correcao) == 2


def test_only_cce_still_shows_note_and_icon_in_html(tmp_path):
    from conftest import authenticated_user
    from fastapi.testclient import TestClient
    from nfe_consulta.web.app import create_app
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    batch(cfg, tmp_path, [cce(), cce(2)])
    with TestClient(create_app(cfg)) as client:
        authenticated_user(client)
        page = client.get('/consulta?chave=' + KEY)
    assert page.status_code == 200
    assert page.text.count('data-cce-open') == 1
    assert '<strong>Carta de Correção</strong>' not in page.text
    assert page.text.count('class="cce-text"') == 2
    assert '07/10/2026 09:01:00' in page.text and CORRECAO in page.text


def test_emission_actions_show_cce_details_without_extra_note_rows(tmp_path):
    from conftest import authenticated_user
    from fastapi.testclient import TestClient
    from nfe_consulta.web.app import create_app
    cfg = web['settings_web'](tmp_path)
    web['criar_banco'](cfg.database_path)
    batch(cfg, tmp_path, [h['helpers']['xml'](), cce(), cce(2)])
    with TestClient(create_app(cfg)) as client:
        authenticated_user(client)
        page = client.get('/xml')
    assert page.status_code == 200
    assert page.text.count('data-cce-open') == 1
    assert page.text.count('class="cce-text"') == 2
    assert '07/10/2026 09:01:00' in page.text and CORRECAO in page.text
    assert 'id="cce-dialog"' in page.text
    assert '1 registro(s)' in page.text
