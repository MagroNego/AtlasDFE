"""Converte a análise de vencimentos do Infor LN exportada em TXT delimitado."""
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils.exceptions import IllegalCharacterError

from nfe_consulta.xlsx_style import configure_sheet, style_cell

MAX_AGING_UPLOAD = 5 * 1024 * 1024
PAGE = re.compile(r"Pág\.\s*:\s*(\d+)")
PARTNER = re.compile(r"^PN faturado\s*:\s*(\S+)\s+(.+?)(?:\s{2,}|$)")
INVOICE = re.compile(r"^([A-Z0-9]+)\s+(\d+)$")
SUBTOTAL = re.compile(r"^Total por PN\s+(\S+)\s+em ML\b")
AMOUNT = re.compile(r"-?\d[\d.]*,\d{2}-?")
HEADERS = [
    "Página", "PN", "Parceiro de negócios", "Tipo", "Fatura", "Documento",
    "Prog.", "Data fatura", "Data vencimento", "Moeda", "Valor fatura",
    "Valor recebido", "Saldo", "Até 30 dias", "31 a 60 dias", "61 a 90 dias",
    "91 a 120 dias", "121 a 180 dias", "Faixa sem rótulo",
]
MONEY_FORMAT = '#,##0.00;[Red](#,##0.00)'


def _amount(value):
    if not value:
        return None
    if not AMOUNT.fullmatch(value) or (value.startswith("-") and value.endswith("-")):
        raise ValueError("Valor monetário inválido.")
    number = Decimal(value.strip("-").replace(".", "").replace(",", "."))
    return -number if "-" in value else number


def _date(value):
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        day, month, year = map(int, value.split("/"))
        return date(year, month, day)
    if not value or re.fullmatch(r"\d{2}/\d{2}/\d{2}", value):
        return value or None
    raise ValueError("Data inválida.")


def _append(sheet, values, row):
    for column, value in enumerate(values, 1):
        cell = sheet.cell(row, column, value)
        if isinstance(cell.value, str):
            cell.data_type = "s"
        style_cell(cell, alternate=cell.row % 2 == 0)
        if isinstance(cell.value, date):
            cell.number_format = "dd/mm/yyyy"
        elif isinstance(cell.value, Decimal):
            cell.number_format = MONEY_FORMAT


def convert_aging(payload: bytes, *, source_note: str | None = None) -> BytesIO:
    try:
        report = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            report = payload.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("Codificação do TXT incompatível.") from exc

    if not re.search(r"\|\s*30 Dias\s*\|\s*60 Dias\s*\|\s*90 Dias\s*\|"
                     r"\s*120 Dias\s*\|\s*180 Dias\s*\|", report, re.I):
        raise ValueError("Cabeçalho de faixas do Aging List do LN não localizado.")

    workbook = Workbook()
    titles = workbook.active
    titles.title = "Lançamentos"
    titles.append(HEADERS)
    checks = workbook.create_sheet("Conferência")
    checks.append([
        "PN", "Títulos", "Fatura dos títulos", "Fatura no LN", "Dif. fatura",
        "Recebido dos títulos", "Recebido no LN", "Dif. recebido",
        "Saldo dos títulos", "Saldo no LN", "Dif. saldo", "Observação",
    ])
    original = workbook.create_sheet("Linhas originais")
    original.append(["Linha", "Página", "Texto do LN"])
    page = None
    pn = partner = ""
    values_by_pn = defaultdict(list)
    subtotals = {}
    total = None
    title_row = 1
    errors = []
    for index, line in enumerate(report.splitlines(), 1):
        if match := PAGE.search(line):
            page = int(match[1])
        try:
            _append(original, [index, page, line], index + 1)
        except IllegalCharacterError:
            errors.append(f"Linha {index}: caractere inválido no TXT.")
            continue
        if match := PARTNER.match(line):
            pn, partner = match.groups()
            continue
        fields = [field.strip() for field in line.split("|")]
        try:
            if match := SUBTOTAL.match(line):
                subtotal_pn = match[1]
                if subtotal_pn in subtotals:
                    raise ValueError("Subtotal por parceiro duplicado.")
                subtotals[subtotal_pn] = tuple(_amount(field) for field in fields[3:6])
            elif line.startswith("Resultado cumulativo"):
                total = tuple(_amount(field) for field in fields[1:4])
            elif match := INVOICE.fullmatch(fields[0]):
                if line[0].isspace():
                    continue
                if len(fields) != 17 or not pn or not fields[6] or not fields[9]:
                    raise ValueError("Colunas do título incompletas.")
                amounts = tuple(_amount(field) for field in fields[7:16])
                if sum(value or 0 for value in amounts[3:]) != amounts[2]:
                    raise ValueError("As faixas de aging divergem do saldo do título.")
                kind, invoice = match.groups()
                invoice_date, due_date = _date(fields[3]), _date(fields[4])
                title_row += 1
                _append(titles, [
                    page, pn, partner, kind, invoice, fields[1], fields[2],
                    invoice_date, due_date, fields[6], *amounts,
                ], title_row)
                values_by_pn[pn].append(amounts)
        except (ValueError, IndexError) as exc:
            errors.append(f"Linha {index}: {exc}")

    if not values_by_pn:
        errors.append("Nenhum título encontrado.")
    for partner_code in sorted(values_by_pn.keys() ^ subtotals.keys()):
        errors.append(f"Parceiro {partner_code}: títulos ou subtotal ausentes.")

    grand_sums = [Decimal(0)] * 3
    check_row = 1
    for pn, rows in values_by_pn.items():
        sums = tuple(sum((row[column] or Decimal(0) for row in rows), Decimal(0))
                     for column in range(3))
        grand_sums = [total_value + subtotal for total_value, subtotal in zip(grand_sums, sums)]
        reported = subtotals.get(pn)
        if reported is None:
            continue
        if len(reported) != 3 or reported[2] is None:
            errors.append(f"Parceiro {pn}: subtotal do LN incompleto.")
            continue
        if sums[2] != reported[2]:
            errors.append(f"Parceiro {pn}: saldo dos títulos {sums[2]:.2f} e subtotal no LN {reported[2]:.2f}.")
        comparison = [value for actual, expected in zip(sums, reported)
                      for value in (actual, expected, actual - (expected or Decimal(0)))]
        observation = ("Revisar fatura/recebimento nas linhas originais."
                       if any(comparison[i] for i in (2, 5)) else "OK")
        check_row += 1
        _append(checks, [pn, len(rows), *comparison, observation], check_row)
    if total is None:
        errors.append("Resultado cumulativo ausente no TXT.")
    elif len(total) != 3 or total[2] is None:
        errors.append("Resultado cumulativo incompleto no TXT.")
    elif grand_sums[2] != total[2]:
        errors.append(f"Saldo dos títulos {grand_sums[2]:.2f} e resultado cumulativo no LN {total[2]}.")
    if errors:
        raise ValueError("\n".join(errors))

    comparison = [value for actual, expected in zip(grand_sums, total)
                  for value in (actual, expected, actual - (expected or Decimal(0)))]
    observation = "Revisar fatura/recebimento." if any(comparison[i] for i in (2, 5)) else "OK"
    _append(checks, ["Total", title_row - 1, *comparison, observation], check_row + 1)
    notes = workbook.create_sheet("Notas da conversão")
    notes.append(["Observação"])
    notes.append([source_note or "Datas de dois dígitos e identificadores são preservados como no TXT."])
    notes.append(["As linhas auxiliares de recebimento e ajuste estão em Linhas originais."])

    for sheet, widths in (
        (titles, [9, 15, 36, 10, 12, 18, 10, 15, 16, 9] + [17] * 9),
        (checks, [18, 12] + [20] * 9 + [55]),
        (notes, [90]),
        (original, [10, 10, 110]),
    ):
        configure_sheet(sheet, header_row=1, widths=widths)
        sheet.freeze_panes = "A2"
        if sheet in (titles, original):
            sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
