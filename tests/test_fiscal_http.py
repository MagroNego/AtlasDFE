import io
import os
import zipfile

import httpx
import pytest
from openpyxl import Workbook, load_workbook
key = '33261112345678000270550010000962031000000000'
xml = f'''<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe Id="NFe{key}">
<ide><nNF>96203</nNF><serie>1</serie><dhEmi>2026-11-13T14:00:00-03:00</dhEmi></ide>
<emit><CNPJ>12345678000270</CNPJ><xNome>Emitente</xNome></emit><dest><xNome>Cliente</xNome></dest>
<det nItem="1"><prod><cProd>ABC</cProd><xProd>Peça</xProd><NCM>72111900</NCM><CFOP>5101</CFOP><uCom>UN</uCom><qCom>2.0000</qCom><vUnCom>15.50</vUnCom><vProd>31.00</vProd></prod>
<imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST><vBC>31.00</vBC><pICMS>20.00</pICMS><vICMS>6.20</vICMS></ICMS00></ICMS></imposto></det>
<total><ICMSTot><vNF>31.00</vNF></ICMSTot><retTrib><vRetPIS>0.31</vRetPIS><vRetCOFINS>1.55</vRetCOFINS></retTrib></total></infNFe></NFe>'''.encode()
@pytest.mark.skipif(not os.getenv("ATLAS_TEST_FISCAL_URL"), reason="API fiscal de teste não configurada")
def test_fiscal_http():
    with httpx.Client(base_url=os.environ["ATLAS_TEST_FISCAL_URL"], trust_env=False) as client:
        assert client.get("/api/emissao").status_code == 401
        t=client.get("/api/sessao").json()["token"]
        assert client.post("/api/login",json={"email":"teste@example.com","senha":"Atlas-Teste-2026!"},headers={"X-CSRF-TOKEN":t}).status_code==204
        h={"X-CSRF-TOKEN":client.get("/api/sessao").json()["token"]}
        def upload(name,data,headers=h):
            return client.post("/api/emissao/xml",files={"arquivos":(name,data)},headers=headers)
        assert upload('nf.xml',xml,{}).status_code == 400
        zipped=io.BytesIO()
        with zipfile.ZipFile(zipped,'w') as archive:
            archive.writestr('sub/nf.xml',xml)
        r=upload('nf.zip',zipped.getvalue())
        assert r.status_code==200,r.text
        assert r.json()=={"importadas":1,"existentes":0}
        assert upload('nf.xml',xml).json()=={"importadas":0,"existentes":1}
        assert upload('nf.xml',xml.replace(b'12345678000270</CNPJ>',b'00000000000000</CNPJ>')).status_code==400
        result=client.get("/api/emissao",params={"busca":"72111900","inicio":"2026-11-13","fim":"2026-11-13"})
        assert result.status_code==200,result.text
        assert [n["valor"] for n in result.json()["notas"]]==[31]
        assert result.json()["notas"][0]["cofinsRetido"] == 1.55
        items=client.get(f"/api/emissao/{key}/itens").json()
        assert items[0]["Vl. ICMS"]==6.2
        for path,title in [("/api/emissao/excel","Emissão"),("/api/consulta/excel?busca=96203","Manifestação")]:
            result=client.get(path)
            assert result.status_code==200,result.text
            wb=load_workbook(io.BytesIO(result.content))
            assert wb[title]["A2"].value==key
        items_file = client.get(f"/api/emissao/{key}/itens/excel")
        assert items_file.status_code == 200
        sheet = load_workbook(io.BytesIO(items_file.content))["Itens"]
        columns = {cell.value: cell.column for cell in sheet[1]}
        assert sheet.cell(2, columns["Vl. ICMS"]).value == 6.2
        aging = (
            "Pág.: 1\n|30 Dias|60 Dias|90 Dias|120 Dias|180 Dias|\n"
            "PN faturado: 001 Empresa\n"
            "NF 000001|DOC|0|13/11/2026|14/11/2026||BRL|31,00||31,00|31,00||||||\n"
            "Total por PN 001 em ML|||31,00||31,00\n"
            "Resultado cumulativo|31,00||31,00\n"
        ).encode()
        result = client.post("/api/aging", files={"arquivo": ("aging.txt", aging)}, headers=h)
        assert result.status_code == 200, result.text
        aging_book = load_workbook(io.BytesIO(result.content))
        assert aging_book["Lançamentos"]["E2"].value == "000001"
        assert aging_book["Conferência"]["I2"].value == 31
        invalid = aging.replace(b"Resultado cumulativo|31,00||31,00", b"Resultado cumulativo|31,00||32,00")
        assert client.post("/api/aging", files={"arquivo": ("aging.txt", invalid)}, headers=h).status_code == 400
        source = Workbook()
        source.active.append(["Análise por vencimento classificada por parceiro de negócios"])
        for line in aging.decode().splitlines():
            source.active.append(line.rstrip("|").split("|"))
        workbook = io.BytesIO()
        source.save(workbook)
        converted = client.post("/api/aging", files={"arquivo": ("aging.xlsx", workbook.getvalue())}, headers=h)
        assert converted.status_code == 200, converted.text
        assert load_workbook(io.BytesIO(converted.content))["Lançamentos"]["E2"].value == "000001"
        corrupt = client.post("/api/aging", files={"arquivo": ("aging.pdf", b"%PDF-1.7\ninvalid")}, headers=h)
        assert corrupt.status_code == 400
        # Word positions follow the LN landscape layout.
        reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
        pdf = io.BytesIO()
        canvas = reportlab.Canvas(pdf, pagesize=(842, 595))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(30, 560, "Análise por vencimento classificada por parceiro de negócios")
        canvas.drawString(750, 560, "Pág.: 1")
        canvas.drawString(30, 530, "30 Dias 60 Dias 90 Dias 120 Dias 180 Dias")
        canvas.drawString(30, 500, "PN faturado: 001")
        canvas.drawString(215, 500, "Empresa")
        for x, value in [(30, "NF 000001"), (110, "DOC"), (150, "0"), (168, "13/11/2026"),
                         (220, "14/11/2026"), (279, "BRL"), (300, "31,00"),
                         (420, "31,00"), (460, "31,00")]:
            canvas.drawString(x, 470, value)
        for y, label in [(440, "Total por PN 001 em ML"), (410, "Resultado cumulativo")]:
            canvas.drawString(30, y, label)
            for x in [300, 420, 460]:
                canvas.drawString(x, y, "31,00")
        canvas.save()
        converted = client.post("/api/aging", files={"arquivo": ("aging.pdf", pdf.getvalue())}, headers=h)
        assert converted.status_code == 200, converted.text
        assert load_workbook(io.BytesIO(converted.content))["Lançamentos"]["E2"].value == "000001"
        assert client.post("/api/admin/fila-nsu", json={"nsu": "55", "alta": True}).status_code == 400
        queued = client.post("/api/admin/fila-nsu", json={"nsu": "55", "alta": True}, headers=h)
        assert queued.status_code == 204, queued.text
        assert any(item["nsu"] == 55 and item["high"] for item in client.get("/api/admin/fila-nsu").json())
        canceled = client.post("/api/admin/fila-nsu", json={"nsu": "55", "cancelar": True}, headers=h)
        assert canceled.status_code == 204, canceled.text
        marking = {"nota": key, "cancelada": True, "motivo": "Conferência interna"}
        assert client.post("/api/admin/situacao", json=marking).status_code == 400
        change = client.post("/api/admin/situacao", json=marking, headers=h)
        assert change.status_code == 204, change.text
        assert client.get("/api/emissao", params={"situacao": "cancelada"}).json()["notas"][0]["chave"] == key
        assert client.get("/api/consulta", params={"busca": key}).json()["notas"][0]["detalhe"]["cancelada"]
        marking["cancelada"] = False
        change = client.post("/api/admin/situacao", json=marking, headers=h)
        assert change.status_code == 204, change.text
        assert not client.get("/api/consulta", params={"busca": key}).json()["notas"][0]["detalhe"]["cancelada"]
        assert [item["cancelada"] for item in client.get("/api/admin/situacao").json()] == [False, True]
