"""Validação do Excel de aging com as linhas e os totais originais do LN."""
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

from nfe_consulta.aging_list import HEADERS, SUBTOTAL, _amount, _append
from nfe_consulta.xlsx_style import configure_sheet

TITLE = re.compile(r"^([A-Z0-9]+)\s+(\d+)\s+")
PARTNER = re.compile(r"^PN faturado\s*:\s*(\S+)")
DATES = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})")
MONEY = re.compile(r"(?<!\S)-?\d[\d.]*,\d{2}-?(?!\S)")
SHEETS = ("Lancamentos", "Linhas originais", "Totais originais")


def _read(payload):
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
                raise ValueError("Planilha descompactada excede 32 MiB.")
        return load_workbook(BytesIO(payload), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise ValueError("Não foi possível ler o XLSX ou o arquivo excede o limite.") from exc


def _original_titles(rows):
    originals = {}
    totals = {}
    pn = ""
    for line_number, page, line in rows:
        if not isinstance(line, str):
            continue
        if partner := PARTNER.match(line):
            pn = partner[1]
        if (subtotal := SUBTOTAL.match(line)) or line.startswith("Resultado cumulativo"):
            key = subtotal[1] if subtotal else "Total"
            amounts = tuple(_amount(value) for value in MONEY.findall(line))
            if key in totals or len(amounts) != 8:
                raise ValueError(f"Linha original {line_number}: total duplicado ou incompleto.")
            totals[key] = amounts
        title = TITLE.match(line)
        dates = DATES.search(line)
        if not (title and dates):
            continue
        fields = re.split(r"\s{2,}", line[title.end():dates.start()].strip())
        suffix = line[dates.end():].split()
        amounts = [_amount(value) for value in MONEY.findall(line[dates.end():])]
        if len(amounts) == 7:
            amounts.insert(1, None)
        currency = next((field for field in suffix if re.fullmatch(r"[A-Z]{3}", field)), None)
        if len(amounts) != 8 or currency is None or not pn or line_number in originals:
            raise ValueError(f"Linha original {line_number}: título incompleto ou duplicado.")
        originals[line_number] = (
            page, pn, *title.groups(), " ".join(fields[:-1]), fields[-1],
            *dates.groups(), currency, *amounts,
        )
    return originals, totals


def convert_aging_xlsx(payload: bytes) -> BytesIO:
    source = _read(payload)
    try:
        if any(name not in source.sheetnames for name in SHEETS):
            raise ValueError("O XLSX deve conter Lancamentos, Linhas originais e Totais originais.")
        titles = source[SHEETS[0]].values
        if tuple(next(titles, ())) != (*HEADERS[:18], "Linha do PDF"):
            raise ValueError("Colunas de Lancamentos incompatíveis com o modelo do Aging.")
        entries = list(titles)
        original_rows = list(source[SHEETS[1]].values)
        total_rows = list(source[SHEETS[2]].values)
    finally:
        source.close()

    originals, totals = _original_titles(original_rows[1:])
    if not originals or "Total" not in totals:
        raise ValueError("Títulos ou resultado cumulativo ausentes nas linhas originais.")
    errors = []
    seen = Counter()
    balances = defaultdict(lambda: [Decimal(0)] * 8)
    for index, row in enumerate(entries, 2):
        if len(row) != 19:
            errors.append(f"Lancamentos, linha {index}: número de colunas inválido.")
            continue
        page, pn, _, kind, invoice, doc, prog, issued, due, currency, *amounts, line_number = row
        seen[line_number] += 1
        expected = originals.get(line_number)
        if expected is None or seen[line_number] > 1:
            errors.append(f"Lancamentos, linha {index}: referência original ausente ou repetida.")
            continue
        formatted_dates = tuple(v.strftime("%d/%m/%Y") if isinstance(v, date) else v for v in (issued, due))
        if any(value is not None and type(value) not in (int, float) for value in amounts):
            errors.append(f"Lancamentos, linha {index}: valor monetário deve ser numérico.")
            continue
        actual = tuple(Decimal(str(value)) if value is not None else None for value in amounts)
        observed = (page, pn, kind, str(invoice), doc or "", str(prog or ""), *formatted_dates, currency, *actual)
        if observed != expected:
            errors.append(f"Lancamentos, linha {index}: {pn} / {kind} {invoice} diverge da linha original {line_number}.")
            continue
        for key in (pn, "Total"):
            for column, value in enumerate(actual):
                balances[key][column] += value or Decimal(0)
    for line_number in originals.keys() - seen.keys():
        _, pn, kind, invoice, *_ = originals[line_number]
        errors.append(f"Título ausente em Lancamentos: {pn} / {kind} {invoice}, linha original {line_number}.")
    if balances.keys() != totals.keys():
        errors.append("Os parceiros dos títulos e dos totais originais não correspondem.")

    checks = []
    for key, reported in totals.items():
        actual = balances[key]
        if actual[2:] != list(reported[2:]):
            errors.append(f"{key}: saldo ou faixas divergentes. Saldo dos títulos {actual[2]:.2f}; LN {reported[2]:.2f}.")
        checks.append((key, actual[0], reported[0], actual[0]-reported[0],
                       actual[1], reported[1], actual[1]-reported[1], actual[2], reported[2],
                       "Revisar faturamento e recebimentos" if actual[:2] != list(reported[:2]) else "OK"))
    if errors:
        raise ValueError("\n".join(errors))

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows, widths in (
        ("Lançamentos", [(*HEADERS[:18], "Linha do PDF"), *entries],
         [9, 15, 36, 10, 12, 18, 10, 15, 16, 9] + [17] * 9),
        ("Conferência", [("PN", "Fatura dos títulos", "Fatura no LN", "Dif. fatura",
                          "Recebido dos títulos", "Recebido no LN", "Dif. recebido",
                          "Saldo dos títulos", "Saldo no LN", "Observação"), *checks],
         [18] + [20] * 8 + [42]),
        ("Linhas originais", original_rows, [10, 10, 110]),
        ("Totais originais", total_rows, [10, 15, 36, 110]),
    ):
        sheet = workbook.create_sheet(name)
        for index, row in enumerate(rows, 1):
            if name == "Lançamentos" and index > 1:
                row = (*row[:10], *(Decimal(str(value)) if value is not None else None
                                    for value in row[10:18]), row[18])
            _append(sheet, row, index)
        configure_sheet(sheet, header_row=1, widths=widths)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    result = BytesIO()
    workbook.save(result)
    result.seek(0)
    return result
