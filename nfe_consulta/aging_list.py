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

    return _export_aging(((line, line) for line in report.splitlines()), source_note=source_note)


def _export_aging(lines, *, source_note=None, source_format="TXT"):
    from_pdf = source_format == "PDF"
    workbook = Workbook()
    titles = workbook.active
    titles.title = "Lançamentos"
    titles.append([*HEADERS, "Linha do PDF"] if from_pdf else HEADERS)
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
    for index, (line, original_line) in enumerate(lines, 1):
        if match := PAGE.search(line):
            page = int(match[1])
        try:
            _append(original, [index, page, original_line], index + 1)
        except IllegalCharacterError:
            errors.append(f"Linha {index}: caractere inválido no {source_format}.")
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
                subtotals[subtotal_pn] = tuple(_amount(field) for field in (fields[3:] if from_pdf else fields[3:6]))
            elif line.startswith("Resultado cumulativo"):
                if from_pdf and total is not None:
                    raise ValueError("Resultado cumulativo duplicado.")
                total = tuple(_amount(field) for field in (fields[1:] if from_pdf else fields[1:4]))
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
                    *([index] if from_pdf else []),
                ], title_row)
                values_by_pn[pn].append(amounts)
        except (ValueError, IndexError) as exc:
            errors.append(f"Linha {index}: {exc}")

    if not values_by_pn:
        errors.append("Nenhum título encontrado.")
    for partner_code in sorted(values_by_pn.keys() ^ subtotals.keys()):
        errors.append(f"Parceiro {partner_code}: títulos ou subtotal ausentes.")

    checked_columns = 9 if from_pdf else 3
    grand_sums = [Decimal(0)] * checked_columns
    check_row = 1
    for pn, rows in values_by_pn.items():
        sums = tuple(sum((row[column] or Decimal(0) for row in rows), Decimal(0))
                     for column in range(checked_columns))
        grand_sums = [total_value + subtotal for total_value, subtotal in zip(grand_sums, sums)]
        reported = subtotals.get(pn)
        if reported is None:
            continue
        if len(reported) != checked_columns or reported[2] is None:
            errors.append(f"Parceiro {pn}: subtotal do LN incompleto.")
            continue
        if sums[2] != reported[2]:
            errors.append(f"Parceiro {pn}: saldo dos títulos {sums[2]:.2f} e subtotal no LN {reported[2]:.2f}.")
        if from_pdf and sums[3:] != tuple(value or 0 for value in reported[3:]):
            errors.append(f"Parceiro {pn}: as faixas de aging divergem do subtotal no LN.")
        comparison = [value for actual, expected in zip(sums[:3], reported[:3])
                      for value in (actual, expected, actual - (expected or Decimal(0)))]
        observation = ("Revisar fatura/recebimento nas linhas originais."
                       if any(comparison[i] for i in (2, 5)) else "OK")
        check_row += 1
        _append(checks, [pn, len(rows), *comparison, observation], check_row)
    if total is None:
        errors.append(f"Resultado cumulativo ausente no {source_format}.")
    elif len(total) != checked_columns or total[2] is None:
        errors.append(f"Resultado cumulativo incompleto no {source_format}.")
    elif grand_sums[2] != total[2]:
        errors.append(f"Saldo dos títulos {grand_sums[2]:.2f} e resultado cumulativo no LN {total[2]}.")
    elif from_pdf and tuple(grand_sums[3:]) != tuple(value or 0 for value in total[3:]):
        errors.append("As faixas de aging divergem do resultado cumulativo no LN.")
    if errors:
        raise ValueError("\n".join(errors))

    comparison = [value for actual, expected in zip(grand_sums[:3], total[:3])
                  for value in (actual, expected, actual - (expected or Decimal(0)))]
    observation = "Revisar fatura/recebimento." if any(comparison[i] for i in (2, 5)) else "OK"
    _append(checks, ["Total", title_row - 1, *comparison, observation], check_row + 1)
    notes = workbook.create_sheet("Notas da conversão")
    notes.append(["Observação"])
    notes.append([source_note or "Datas de dois dígitos e identificadores são preservados como no TXT."])
    notes.append(["As linhas auxiliares de recebimento e ajuste estão em Linhas originais."])

    for sheet, widths in (
        (titles, [9, 15, 36, 10, 12, 18, 10, 15, 16, 9] + [17] * (10 if from_pdf else 9)),
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
