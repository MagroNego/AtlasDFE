using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using ClosedXML.Excel;

internal static partial class AgingTxt
{
    private static readonly CultureInfo Pt = CultureInfo.GetCultureInfo("pt-BR");

    public static byte[] ConvertLnWorkbook(byte[] bytes, CancellationToken token)
    {
        using var payload = new MemoryStream(bytes, writable: false);
        using (var zip = new System.IO.Compression.ZipArchive(payload, System.IO.Compression.ZipArchiveMode.Read, leaveOpen: true))
            if (zip.Entries.Count > 2000 || zip.Entries.Sum(entry => entry.Length) > 32 * 1024 * 1024)
                throw new InvalidDataException("O XLSX expandido excede o limite.");
        payload.Position = 0;
        using var book = new XLWorkbook(payload);
        if (book.Worksheets.Count != 1)
            throw new InvalidDataException("Envie o XLSX de uma aba exportado pelo Infor LN.");
        var source = book.Worksheet(1);
        if (!source.Cell(1, 1).GetString().Contains("Análise por vencimento classificada por parceiro", StringComparison.Ordinal))
            throw new InvalidDataException("Cabeçalho do Aging List do LN não localizado.");
        int rows = source.LastRowUsed()?.RowNumber() ?? 0;
        int columns = source.LastColumnUsed()?.ColumnNumber() ?? 0;
        if (rows > 100000 || columns > 32)
            throw new InvalidDataException("O XLSX excede o limite de linhas ou colunas.");
        var lines = new StringBuilder();
        for (int row = 1; row <= rows; row++)
        {
            token.ThrowIfCancellationRequested();
            int last = source.Row(row).LastCellUsed()?.Address.ColumnNumber ?? 0;
            // The original LN layout uses 16 fields for titles, including optional blank bands.
            string first = source.Cell(row, 1).GetString().Trim();
            bool title = Title().IsMatch(first);
            if (title && last > 16)
                throw new InvalidDataException($"Linha {row}: colunas extras no título.");
            if (title)
                last = 16;
            for (int col = 1; col <= last; col++)
            {
                var cell = source.Cell(row, col);
                if (cell.HasFormula)
                    throw new InvalidDataException($"Linha {row}: fórmulas não são aceitas.");
                var value = cell.Value;
                string field = value.Type switch
                {
                    XLDataType.Blank => "",
                    XLDataType.DateTime => value.GetDateTime().ToString("dd/MM/yyyy", Pt),
                    XLDataType.Number when double.IsFinite(value.GetNumber()) &&
                        Math.Round(value.GetNumber(), 2) == value.GetNumber() => value.GetNumber().ToString("0.00", Pt),
                    XLDataType.Text => value.GetText(),
                    _ => throw new InvalidDataException($"Linha {row}: valor inválido no XLSX."),
                };
                if (field.IndexOfAny(['|', '\r', '\n']) >= 0)
                    throw new InvalidDataException($"Linha {row}: separador inesperado no XLSX.");
                if (col > 1) lines.Append('|');
                lines.Append(field);
            }
            lines.AppendLine("|");
        }
        return Convert(Encoding.UTF8.GetBytes(lines.ToString()), token);
    }

    public static byte[] Convert(byte[] bytes, CancellationToken token)
    {
        string text;
        try { text = new UTF8Encoding(false, true).GetString(bytes).TrimStart('\uFEFF'); }
        catch (DecoderFallbackException)
        {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            text = Encoding.GetEncoding(1252).GetString(bytes);
        }
        if (!Bands().IsMatch(text))
            throw new InvalidDataException("Cabeçalho de faixas do Aging List do LN não localizado.");

        using var input = new StringReader(text);
        var lines = new List<(string Parsed, string Original)>();
        while (input.ReadLine() is { } line)
            lines.Add((line, line));
        return Export(lines, token);
    }

    internal static byte[] Export(IEnumerable<(string Parsed, string Original)> lines, CancellationToken token, bool pdf = false)
    {
        using var book = new XLWorkbook();
        var titles = Sheet(book, "Lançamentos", ["Página", "PN", "Parceiro de negócios", "Tipo", "Fatura", "Documento",
            "Prog.", "Data fatura", "Data vencimento", "Moeda", "Valor fatura", "Valor recebido", "Saldo",
            "Até 30 dias", "31 a 60 dias", "61 a 90 dias", "91 a 120 dias", "121 a 180 dias", "Faixa sem rótulo"]);
        var checks = Sheet(book, "Conferência", ["PN", "Títulos", "Fatura dos títulos", "Fatura no LN", "Dif. fatura",
            "Recebido dos títulos", "Recebido no LN", "Dif. recebido", "Saldo dos títulos", "Saldo no LN", "Dif. saldo", "Observação"]);
        var originals = Sheet(book, "Linhas originais", ["Linha", "Página", "Texto do LN"]);
        originals.Column(3).Width = 100;
        int checkedColumns = pdf ? 9 : 3;
        var entries = new Dictionary<string, (int Count, decimal[] Sums)>();
        var totals = new Dictionary<string, decimal?[]>();
        decimal?[]? grand = null;
        string partner = "", name = "";
        int? page = null;
        int lineNumber = 0, titleRow = 1;
        foreach (var (line, original) in lines)
        {
            token.ThrowIfCancellationRequested();
            if (++lineNumber > 100000 || line.Length > 32767)
                throw new InvalidDataException("O Aging excede o limite de linhas ou de caracteres por linha.");
            var pageMatch = Page().Match(line);
            if (pageMatch.Success && int.TryParse(pageMatch.Groups[1].Value, out int printed))
                page = printed;
            Row(originals, lineNumber + 1, [lineNumber, page, original]);
            var match = Partner().Match(line);
            if (match.Success)
            {
                partner = match.Groups[1].Value;
                name = match.Groups[2].Value;
                continue;
            }
            var fields = line.Split('|', StringSplitOptions.TrimEntries);
            var subtotal = Subtotal().Match(line);
            if (subtotal.Success)
            {
                if (!totals.TryAdd(subtotal.Groups[1].Value, fields.Skip(3).Take(checkedColumns).Select(Amount).ToArray()))
                    throw new InvalidDataException($"Linha {lineNumber}: subtotal duplicado.");
                continue;
            }
            if (line.StartsWith("Resultado cumulativo", StringComparison.Ordinal))
            {
                if (grand is not null)
                    throw new InvalidDataException($"Linha {lineNumber}: resultado cumulativo duplicado.");
                grand = fields.Skip(1).Take(checkedColumns).Select(Amount).ToArray();
                continue;
            }

            var title = Title().Match(fields[0]);
            if (!title.Success || char.IsWhiteSpace(line[0]))
                continue;
            if (fields.Length != 17 || partner.Length == 0 || fields[6].Length == 0)
                throw new InvalidDataException($"Linha {lineNumber}: colunas do título incompletas.");
            var amounts = fields.Skip(7).Take(9).Select(Amount).ToArray();
            if (amounts[2] is null || amounts.Skip(3).Sum(value => value ?? 0) != amounts[2])
                throw new InvalidDataException($"Linha {lineNumber}: faixas de aging divergem do saldo.");
            var current = entries.TryGetValue(partner, out var previous) ? previous : (Count: 0, Sums: new decimal[checkedColumns]);
            for (int i = 0; i < checkedColumns; i++)
                current.Sums[i] += amounts[i] ?? 0;
            entries[partner] = (current.Count + 1, current.Sums);
            Row(titles, ++titleRow, [page, partner, name, title.Groups[1].Value, title.Groups[2].Value,
                fields[1], fields[2], Date(fields[3]), Date(fields[4]), fields[6], .. amounts.Cast<object?>()]);
        }

        if (entries.Count == 0 || !entries.Keys.ToHashSet().SetEquals(totals.Keys) || grand is null)
            throw new InvalidDataException("Títulos, subtotais ou resultado cumulativo ausentes.");
        var sum = new decimal[checkedColumns];
        int checkRow = 1;
        foreach (var (code, values) in entries)
        {
            Verify(code, values.Count, values.Sums, totals[code]);
            for (int i = 0; i < checkedColumns; i++) sum[i] += values.Sums[i];
        }
        Verify("Total", titleRow - 1, sum, grand);
        using var output = new MemoryStream();
        book.SaveAs(output);
        return output.ToArray();

        void Verify(string code, int count, decimal[] actual, decimal?[] reported)
        {
            if (reported.Length != checkedColumns || actual[2] != reported[2] ||
                pdf && !actual.Skip(3).SequenceEqual(reported.Skip(3).Select(amount => amount ?? 0)))
                throw new InvalidDataException($"PN {code}: saldo dos títulos diverge do total no LN.");
            Row(checks, ++checkRow, [code, count, actual[0], reported[0], actual[0] - (reported[0] ?? 0),
                actual[1], reported[1], actual[1] - (reported[1] ?? 0), actual[2], reported[2], 0,
                actual[0] == reported[0] && actual[1] == reported[1] ? "OK" : "Revisar fatura/recebimento"]);
        }
    }

    private static IXLWorksheet Sheet(XLWorkbook book, string name, string[] fields)
    {
        var sheet = book.AddWorksheet(name);
        Row(sheet, 1, fields);
        sheet.Row(1).Style.Font.Bold = true;
        sheet.Columns().Width = 20;
        sheet.SheetView.FreezeRows(1);
        return sheet;
    }

    private static void Row(IXLWorksheet sheet, int row, object?[] fields)
    {
        for (int i = 0; i < fields.Length; i++)
        {
            var cell = sheet.Cell(row, i + 1);
            switch (fields[i])
            {
                case string text: cell.Value = text; break;
                case int number: cell.Value = number; break;
                case decimal amount: cell.Value = amount; cell.Style.NumberFormat.Format = "#,##0.00"; break;
                case DateTime date: cell.Value = date; cell.Style.DateFormat.Format = "dd/mm/yyyy"; break;
            }
        }
    }

    private static object? Date(string value)
    {
        if (value.Length == 0)
            return null;
        if (DateTime.TryParseExact(value, "dd/MM/yyyy", Pt, DateTimeStyles.None, out var date))
            return date;
        if (ShortDate().IsMatch(value))
            return value;
        throw new InvalidDataException("Data inválida no Aging.");
    }

    private static decimal? Amount(string input)
    {
        if (input.Length == 0)
            return null;
        if (!Money().IsMatch(input) || (input.StartsWith('-') && input.EndsWith('-'))
            || !decimal.TryParse(input.Trim('-'), NumberStyles.AllowDecimalPoint | NumberStyles.AllowThousands,
                Pt, out decimal value))
            throw new InvalidDataException("Valor monetário inválido no Aging.");
        return input.Contains('-') ? -value : value;
    }

    [GeneratedRegex(@"\|\s*30 Dias\s*\|\s*60 Dias\s*\|\s*90 Dias\s*\|\s*120 Dias\s*\|\s*180 Dias\s*\|", RegexOptions.IgnoreCase)]
    private static partial Regex Bands();
    [GeneratedRegex(@"Pág\.\s*:\s*(\d+)")]
    private static partial Regex Page();
    [GeneratedRegex(@"^PN faturado\s*:\s*(\S+)\s+(.+?)(?:\s{2,}|$)")]
    private static partial Regex Partner();
    [GeneratedRegex(@"^Total por PN\s+(\S+)\s+em ML\b")]
    private static partial Regex Subtotal();
    [GeneratedRegex(@"^([A-Z0-9]+)\s+(\d+)$")]
    private static partial Regex Title();
    [GeneratedRegex(@"^\d{2}/\d{2}/\d{2}$")]
    private static partial Regex ShortDate();
    [GeneratedRegex(@"^-?\d[\d.]*,\d{2}-?$")]
    private static partial Regex Money();
}
