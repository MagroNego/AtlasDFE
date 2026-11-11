"""Lê o relatório de vencimentos do Infor LN exportado em PDF."""
import re
from bisect import bisect_right
from io import BytesIO

import pdfplumber
from pdfminer.pdfexceptions import PSException
from pdfplumber.utils import extract_text
from pdfplumber.utils.exceptions import PdfminerException

from nfe_consulta.aging_list import INVOICE, PAGE, SUBTOTAL, _export_aging

REPORT_TITLE = "Análise por vencimento classificada por parceiro de negócios"
BANDS = "30 Dias 60 Dias 90 Dias 120 Dias 180 Dias"
PARTNER = re.compile(r"^PN faturado\s*:\s*(\S+)")
TITLE_START = re.compile(r"^[A-Z0-9]+\s+\d+\b")
TITLE_DATES = re.compile(r"\d{2}/\d{2}/\d{2,4}\s+\d{2}/\d{2}/\d{2,4}")

# Limites em pontos do layout A4 paisagem do LN. Datas contíguas e células
# vazias precisam ser lidas pela posição, inclusive o recebido em branco.
COLUMN_BREAKS = (104, 148, 166, 203, 239, 272, 294, 347, 400, 453,
                 505, 553, 603, 657, 716, 764)


def _columns(chars):
    columns = [[] for _ in range(len(COLUMN_BREAKS) + 1)]
    for char in chars:
        columns[bisect_right(COLUMN_BREAKS, char["x0"])].append(char)
    return [extract_text(column, x_tolerance=1).strip() for column in columns]


def _page_rows(page, page_number):
    lines = page.extract_text_lines(x_tolerance=1)
    header = lines[0]["text"] if lines else ""
    printed_page = PAGE.search(header)
    if (REPORT_TITLE not in header or printed_page is None or int(printed_page[1]) != page_number
            or not any(BANDS in line["text"] for line in lines)):
        raise ValueError(f"Página {page_number}: cabeçalho ou paginação incompatível com o Aging do LN.")

    for line in lines:
        original = line["text"]
        chars = line["chars"]
        if partner := PARTNER.match(original):
            name_chars = [char for char in chars if 212 <= char["x0"] < 329]
            name = extract_text(name_chars, x_tolerance=1).strip()
            yield f"PN faturado: {partner[1]} {name}", original
        elif SUBTOTAL.match(original) or original.startswith("Resultado cumulativo"):
            amounts = _columns(chars)[7:16]
            prefix = original.split("em ML", 1)[0].strip()
            if original.startswith("Resultado cumulativo"):
                yield "|".join((prefix, *amounts)), original
            else:
                yield "|".join((f"{prefix} em ML", "", "", *amounts)), original
        elif TITLE_START.match(original) and (chars[0]["x0"] < 59 or TITLE_DATES.search(original)):
            fields = _columns(chars)
            if not INVOICE.fullmatch(fields[0]) or not re.fullmatch(r"[A-Z]{3}", fields[6]):
                raise ValueError(f"Página {page_number}: colunas do título incompletas.")
            yield "|".join(fields), original
        else:
            yield original, original


def _report_rows(payload):
    if not payload.startswith(b"%PDF-"):
        raise ValueError("Selecione o PDF original exportado pelo Infor LN.")
    try:
        with pdfplumber.open(BytesIO(payload)) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                yield from _page_rows(page, number)
                page.close()
    except (PdfminerException, PSException, OSError) as exc:
        raise ValueError("Não foi possível ler o PDF. Exporte o relatório novamente no Infor LN.") from exc


def convert_aging_pdf(payload: bytes) -> BytesIO:
    return _export_aging(
        _report_rows(payload),
        source_note="Dados extraídos do PDF original do Infor LN. Documentos em branco foram preservados.",
        source_format="PDF",
    )
