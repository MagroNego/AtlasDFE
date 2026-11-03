from datetime import date, time
from io import BytesIO
import json
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from conftest import authenticated_user
from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.web import xml_store as store
from nfe_consulta.web.app import create_app

h = runpy.run_path(str(Path(__file__).with_name('test_cancelamento_xml.py')))


def test_emission_export_adds_summary_with_numeric_values_and_current_status(tmp_path):
    cfg = h['web']['settings_web'](tmp_path)
    h['web']['criar_banco'](cfg.database_path)
    payload = h['helpers']['xml']().replace(b'</infNFe>', b'<transp><vol><pesoL>1.250</pesoL><pesoB>1.500</pesoB></vol><vol><pesoL>2.000</pesoL><pesoB>2.500</pesoB></vol></transp></infNFe>')
    h['batch'](cfg, tmp_path, [payload, h['cancellation']()])
    with TestClient(create_app(cfg)) as client:
        authenticated_user(client)
        response = client.get('/xml/exportar', params={'formato': 'xlsx', 'tipo': 'notas', 'situacao': 'cancelada'})
        assert response.status_code == 200
        book = load_workbook(BytesIO(response.content))
        assert book.sheetnames == ['Notas', 'Itens', 'Retencoes', 'Resumo']
        sheet = book['Resumo']
        assert [c.value for c in sheet[1]] == ['Número de nota', 'Item', 'Quantidade', 'Preço total', 'CFOP', 'Data', 'Hora', 'Peso líq.', 'Peso bruto', 'Status']
        row = [c.value for c in sheet[2]]
        assert row[:5] == ['123', '00007', 3, 0.37, '5102']
        assert row[5].date() == date(2026, 10, 1)
        assert row[6] == time(10)
        assert row[7:] == [3.25, 4, 'Cancelada']
        assert sheet.freeze_panes == 'A2'
        assert sheet.auto_filter.ref == 'A1:J2'
        assert book['Itens'].max_row == sheet.max_row == 2
        filtered = client.get('/xml/exportar', params={'formato': 'xlsx', 'tipo': 'notas', 'situacao': 'autorizada'})
        assert load_workbook(BytesIO(filtered.content))['Resumo'].max_row == 1


def test_reimport_enriches_only_missing_metadata_and_keeps_original_items(tmp_path):
    cfg = h['web']['settings_web'](tmp_path)
    h['web']['criar_banco'](cfg.database_path)
    payload = h['helpers']['xml']()
    h['batch'](cfg, tmp_path, [payload])
    conn = abrir_banco(cfg.database_path)
    try:
        records = conn.execute("SELECT linha,dados FROM xml_relatorio WHERE tipo='itens'").fetchall()
        with conn:
            for line, data in records:
                row = json.loads(data)
                for field in ('Hora Emissao', 'Peso Liquido', 'Peso Bruto'):
                    row.pop(field, None)
                conn.execute("UPDATE xml_relatorio SET dados=? WHERE tipo='itens' AND linha=?", (json.dumps(row), line))
    finally:
        conn.close()
    before = store.query_report(cfg.database_path, CNPJ_PADRAO, kind='itens')['rows'][0]
    newer = payload.replace(b'</infNFe>', b'<transp><vol><pesoL>0</pesoL><pesoB>1.500</pesoB></vol></transp></infNFe>').replace(b'0.37</vProd>', b'99.99</vProd>')
    result = h['batch'](cfg, tmp_path, [newer, h['cancellation']()])
    assert result['duplicadas'] == result['cancelamentos'] == 1
    after = store.query_report(cfg.database_path, CNPJ_PADRAO, kind='itens')['rows'][0]
    assert after['Vl. Total'] == before['Vl. Total']
    assert after['Hora Emissao'] == '10:00:00'
    assert after['Peso Liquido'] == '0'
    assert after['Peso Bruto'] == '1,5'
    assert after['Situação'] == 'Cancelada'


def test_legacy_metadata_stays_blank_in_summary():
    report = store.emission_summary({'rows': [{'Numero Nota': '123', 'Item': '1', 'Codigo': '=1+1', 'Qtd': '2', 'Vl. Total': '10,5', 'CFOP': '5102', 'Data Emissao': '01/10/2026', 'Situação': 'Autorizada'}]})
    book = load_workbook(BytesIO(store.export_excel([('Resumo', report)])))
    sheet = book['Resumo']
    assert sheet['B2'].value == '=1+1' and sheet['B2'].data_type == 's'
    assert sheet['G2'].value is sheet['H2'].value is sheet['I2'].value is None


def test_bad_duplicate_weight_does_not_rollback_other_documents(tmp_path):
    cfg = h['web']['settings_web'](tmp_path)
    h['web']['criar_banco'](cfg.database_path)
    original = h['helpers']['xml']()
    h['batch'](cfg, tmp_path, [original])
    invalid = original.replace(b'</infNFe>', b'<transp><vol><pesoL>1E101</pesoL></vol></transp></infNFe>')
    other_key = h['KEY'][:25] + '000000124' + h['KEY'][34:]
    result = h['batch'](cfg, tmp_path, [invalid, h['helpers']['xml'](chave=other_key), h['cancellation']()])
    assert result['erros'] == result['importadas'] == result['cancelamentos'] == 1
    assert store.query_report(cfg.database_path, CNPJ_PADRAO)['total'] == 2
