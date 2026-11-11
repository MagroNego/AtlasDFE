"""Lê o relatório de vencimentos do Infor LN exportado em PDF."""
import re
import sys
from bisect import bisect_right
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from nfe_consulta.aging_list import INVOICE, PAGE, SUBTOTAL, _export_aging

# Python pode importar o código-fonte do leitor diretamente do ZIP.
_READER_ARCHIVE = str(Path(__file__).resolve().with_name("_vendor") / "pypdf-6.21.4.zip")
if _READER_ARCHIVE not in sys.path:
    sys.path.insert(0, _READER_ARCHIVE)

from pypdf import PdfReader
from pypdf.errors import PyPdfError

REPORT_TITLE = "Análise por vencimento classificada por parceiro de negócios"
BANDS = "30 Dias 60 Dias 90 Dias 120 Dias 180 Dias"
PARTNER = re.compile(r"^PN faturado\s*:\s*(\S+)")
TITLE_START = re.compile(r"^[A-Z0-9]+\s+\d+\b")
TITLE_DATES = re.compile(r"\d{2}/\d{2}/\d{2,4}\s+\d{2}/\d{2}/\d{2,4}")

# Limites em pontos do layout A4 paisagem do LN. Datas contíguas e células
# vazias precisam ser lidas pela posição, inclusive o recebido em branco.
COLUMN_BREAKS = (104, 148, 166, 203, 239, 272, 294, 347, 400, 453,
                 505, 553, 603, 657, 716, 764)


def _columns(fragments):
    columns = [[] for _ in range(len(COLUMN_BREAKS) + 1)]
    for x, fragment in fragments:
        columns[bisect_right(COLUMN_BREAKS, x)].append(fragment)
    fields = [" ".join(column) for column in columns]
    fields[7:16] = [re.sub(r"(?<=\d) -$", "-", field) for field in fields[7:16]]
    return fields


def _page_lines(page):
    fragments_by_y = defaultdict(list)

    def collect(text, matrix, position, _font, _size):
        if fragment := " ".join(text.split()):
            x = position[4] * matrix[0] + position[5] * matrix[2] + matrix[4]
            y = position[4] * matrix[1] + position[5] * matrix[3] + matrix[5]
            fragments_by_y[round(y, 1)].append((x, fragment))

    page.extract_text(visitor_text=collect)
    for y in sorted(fragments_by_y, reverse=True):
        fragments = sorted(fragments_by_y[y], key=lambda item: item[0])
        yield " ".join(fragment for _, fragment in fragments), fragments


def _page_rows(page, page_number):
    lines = list(_page_lines(page))
    header = lines[0][0] if lines else ""
    printed_page = PAGE.search(header)
    if (REPORT_TITLE not in header or printed_page is None or int(printed_page[1]) != page_number
            or not any(BANDS in line for line, _ in lines)):
        raise ValueError(f"Página {page_number}: cabeçalho ou paginação incompatível com o Aging do LN.")

    for original, fragments in lines:
        if partner := PARTNER.match(original):
            name = " ".join(fragment for x, fragment in fragments if 212 <= x < 329)
            yield f"PN faturado: {partner[1]} {name}", original
        elif SUBTOTAL.match(original) or original.startswith("Resultado cumulativo"):
            amounts = _columns(fragments)[7:16]
            prefix = original.split("em ML", 1)[0].strip()
            if original.startswith("Resultado cumulativo"):
                yield "|".join((prefix, *amounts)), original
            else:
                yield "|".join((f"{prefix} em ML", "", "", *amounts)), original
        elif TITLE_START.match(original) and (fragments[0][0] < 59 or TITLE_DATES.search(original)):
            fields = _columns(fragments)
            if not INVOICE.fullmatch(fields[0]) or not re.fullmatch(r"[A-Z]{3}", fields[6]):
                raise ValueError(f"Página {page_number}: colunas do título incompletas.")
            yield "|".join(fields), original
        else:
            yield original, original


def _report_rows(payload):
    if not payload.startswith(b"%PDF-"):
        raise ValueError("Selecione o PDF original exportado pelo Infor LN.")
    try:
        with PdfReader(BytesIO(payload)) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                yield from _page_rows(page, number)
    except (PyPdfError, OSError) as exc:
        raise ValueError("Não foi possível ler o PDF. Exporte o relatório novamente no Infor LN.") from exc


def convert_aging_pdf(payload: bytes) -> BytesIO:
    return _export_aging(
        _report_rows(payload),
        source_note="Dados extraídos do PDF original do Infor LN. Documentos em branco foram preservados.",
        source_format="PDF",
    )
