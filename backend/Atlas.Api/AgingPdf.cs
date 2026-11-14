using System.Text.RegularExpressions;
using UglyToad.PdfPig;
using UglyToad.PdfPig.Content;

internal static partial class AgingPdf
{
    private static readonly int[] Breaks = [104,148,166,203,239,272,294,347,400,453,505,553,603,657,716,764];

    public static byte[] Convert(byte[] input, CancellationToken token)
    {
        if (!input.AsSpan().StartsWith("%PDF-"u8))
            throw new InvalidDataException("Envie o PDF original exportado pelo Infor LN.");
        try
        {
            using var document = PdfDocument.Open(input);
            if (document.NumberOfPages is < 1 or > 500)
                throw new InvalidDataException("O PDF tem quantidade de páginas inválida.");
            return AgingTxt.Export(ReadPages(document, token), token, pdf: true);
        }
        catch (Exception error) when (error is not (InvalidDataException or OperationCanceledException or OutOfMemoryException))
        {
            throw new InvalidDataException("Não foi possível ler o PDF do Infor LN.", error);
        }
    }

    private static IEnumerable<(string Parsed, string Original)> ReadPages(PdfDocument document, CancellationToken token)
    {
        foreach (var page in document.GetPages())
        {
            token.ThrowIfCancellationRequested();
            var lines = page.GetWords()
                .GroupBy(word => Math.Round(word.BoundingBox.Bottom, 0))
                .OrderByDescending(group => group.Key)
                .Select(group => group.OrderBy(word => word.BoundingBox.Left).ToArray())
                .ToArray();
            var text = lines.Select(Line).ToArray();
            string header = string.Join(" ", text.Take(8));
            if (!header.Contains("Análise por vencimento classificada por parceiro de negócios", StringComparison.Ordinal)
                || !text.Any(line => line.Contains("30 Dias 60 Dias 90 Dias 120 Dias 180 Dias", StringComparison.Ordinal))
                || !PageNumber().Matches(header).Any(match => match.Groups[1].Value == page.Number.ToString(System.Globalization.CultureInfo.InvariantCulture)))
                throw new InvalidDataException($"Página {page.Number}: cabeçalho ou paginação incompatível com o Aging do LN.");

            yield return ($"Pág.: {page.Number}", "");
            for (int i = 0; i < lines.Length; i++)
            {
                string original = text[i];
                var match = Partner().Match(original);
                string parsed = original;
                if (match.Success)
                {
                    string name = Line(lines[i].Where(word => word.BoundingBox.Left is >= 212 and < 329));
                    if (name.Length == 0) name = match.Groups[2].Value;
                    parsed = $"PN faturado: {match.Groups[1].Value} {name}";
                }
                else if (original.StartsWith("Total por PN ", StringComparison.Ordinal)
                    || original.StartsWith("Resultado cumulativo", StringComparison.Ordinal))
                {
                    string prefix = original.Split("em ML", 2)[0].Trim();
                    string fields = string.Join('|', Columns(lines[i]).Skip(7).Take(9));
                    parsed = original.StartsWith("Total por PN ", StringComparison.Ordinal)
                        ? $"{prefix} em ML|||{fields}" : $"Resultado cumulativo|{fields}";
                }
                else if (Title().IsMatch(original) && lines[i][0].BoundingBox.Left < 59)
                    parsed = string.Join('|', Columns(lines[i]));
                yield return (parsed, original);
            }
        }
    }

    private static string[] Columns(IEnumerable<Word> words)
    {
        var groups = Enumerable.Range(0, Breaks.Length + 1).Select(_ => new List<Word>()).ToArray();
        foreach (var word in words)
        {
            int index = Array.FindIndex(Breaks, boundary => word.BoundingBox.Left < boundary);
            groups[index < 0 ? Breaks.Length : index].Add(word);
        }
        var fields = groups.Select(wordsInColumn => Line(wordsInColumn).Replace(" -", "-", StringComparison.Ordinal)).ToArray();
        if (fields[4].Length == 0 && Dates().Match(fields[3]) is { Success: true } dates)
        {
            fields[3] = dates.Groups[1].Value;
            fields[4] = dates.Groups[2].Value;
        }
        return fields;
    }

    private static string Line(IEnumerable<Word> words) => string.Join(" ", words.Select(word => word.Text));
    [GeneratedRegex(@"^PN faturado\s*:\s*(\S+)\s+(.*)")]
    private static partial Regex Partner();
    [GeneratedRegex(@"^[A-Z0-9]+\s+\d+\b")]
    private static partial Regex Title();
    [GeneratedRegex(@"^(\d{2}/\d{2}/\d{4})\s*(\d{2}/\d{2}/\d{4})$")]
    private static partial Regex Dates();
    [GeneratedRegex(@"Pág\.\s*:\s*(\d+)")]
    private static partial Regex PageNumber();
}
