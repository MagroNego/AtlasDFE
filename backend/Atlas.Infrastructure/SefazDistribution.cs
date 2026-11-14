using System.Globalization;
using System.IO.Compression;
using System.Net.Http.Headers;
using System.Xml;
using System.Xml.Linq;

namespace Atlas.Infrastructure;

public sealed record SefazDocument(long Nsu, string Schema, byte[] Xml);

public sealed record SefazBatch(
    int Status, string Reason, long? LastNsu, long? MaxNsu, IReadOnlyList<SefazDocument> Documents)
{
    public long CursorAfter(long cursor, bool recovery = false, long? requested = null)
    {
        if (Status is not (137 or 138))
            return cursor;

        if (recovery)
        {
            if (Status == 138 && (Documents.Count != 1 || Documents[0].Nsu != requested))
                throw new InvalidDataException("A resposta de recuperação diverge do NSU solicitado.");
            return cursor;
        }

        if (LastNsu is not long last || MaxNsu is not long max || last < cursor || last > max
            || (Status == 137 && last != max)
            || (Status == 138 && (last == cursor || Documents.Any(d => d.Nsu <= cursor || d.Nsu > last))))
            throw new InvalidDataException("Recebidos NSUs inconsistentes. O cursor foi preservado.");

        return last;
    }

    public DateTimeOffset? RetryAt(DateTimeOffset receivedAt) =>
        Status is 137 or 656 || (Status == 138 && LastNsu == MaxNsu) ? receivedAt.AddHours(1) : null;
}

public static class SefazDistribution
{
    public static readonly Uri ProductionEndpoint =
        new("https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx");
    private const string Action = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse";
    private static readonly XNamespace Nfe = "http://www.portalfiscal.inf.br/nfe";
    private static readonly XNamespace Wsdl = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe";
    private static readonly XNamespace Soap = "http://www.w3.org/2003/05/soap-envelope";
    private const int MaxSoap = 32 * 1024 * 1024;
    private const int MaxZip = 2 * 1024 * 1024;
    private const int MaxXml = 8 * 1024 * 1024;
    private const int MaxBatchXml = 64 * 1024 * 1024;
    private const long MaxNsu = 999_999_999_999_999;

    public static string Request(string cnpj, string uf, long nsu, bool recovery)
    {
        if (cnpj.Length != 14 || !cnpj.All(char.IsAsciiDigit))
            throw new ArgumentException("Informe os 14 dígitos do CNPJ.", nameof(cnpj));
        if (uf.Length != 2 || !uf.All(char.IsAsciiDigit))
            throw new ArgumentException("Informe o código numérico da UF.", nameof(uf));
        if (nsu is < 0 or > MaxNsu || (recovery && nsu == 0))
            throw new ArgumentOutOfRangeException(nameof(nsu));

        var query = new XElement(Nfe + (recovery ? "consNSU" : "distNSU"),
            new XElement(Nfe + (recovery ? "NSU" : "ultNSU"), nsu.ToString("D15", CultureInfo.InvariantCulture)));
        var payload = new XElement(Nfe + "distDFeInt", new XAttribute("versao", "1.01"),
            new XElement(Nfe + "tpAmb", 1), new XElement(Nfe + "cUFAutor", uf), new XElement(Nfe + "CNPJ", cnpj), query);
        return new XElement(Soap + "Envelope",
            new XElement(Soap + "Body",
                new XElement(Wsdl + "nfeDistDFeInteresse", new XElement(Wsdl + "nfeDadosMsg", payload))))
            .ToString(SaveOptions.DisableFormatting);
    }

    // Entrega o corpo bruto ao chamador para persistência antes do parser.
    // O HttpClient é configurado pelo chamador com certificado e validação TLS.
    public static async Task<byte[]> SendAsync(
        HttpClient client, string soap, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, ProductionEndpoint)
        {
            Content = new StringContent(soap, System.Text.Encoding.UTF8, "application/soap+xml"),
        };
        request.Content.Headers.ContentType!.Parameters.Add(new NameValueHeaderValue("action", $"\"{Action}\""));
        using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        return await ReadBoundedAsync(stream, MaxSoap, cancellationToken);
    }

    public static SefazBatch Parse(string soap) => Parse(System.Text.Encoding.UTF8.GetBytes(soap));

    public static SefazBatch Parse(byte[] soap)
    {
        var envelope = ReadXml(soap, MaxSoap);
        var root = Single(envelope.DescendantsAndSelf(Nfe + "retDistDFeInt"));
        if (root is null)
        {
            var wrapped = Single(envelope.Descendants(Wsdl + "nfeDistDFeInteresseResult"));
            if (wrapped is not null && !wrapped.HasElements)
                root = ReadXml(System.Text.Encoding.UTF8.GetBytes(wrapped.Value.Trim()), MaxSoap);
        }
        if (root?.Name != Nfe + "retDistDFeInt" ||
            !int.TryParse(Value(root, "cStat"), NumberStyles.None, CultureInfo.InvariantCulture, out int status)
            || status is < 100 or > 999)
            throw new InvalidDataException("A resposta da SEFAZ está sem retorno ou cStat válido.");

        long? last = ReadNsu(Value(root, "ultNSU"));
        long? max = ReadNsu(Value(root, "maxNSU"));
        if (status is 137 or 138 && (last is null || max is null || last > max))
            throw new InvalidDataException("A resposta da SEFAZ está sem ultNSU/maxNSU válidos.");

        var documents = new List<SefazDocument>();
        var seen = new HashSet<long>();
        int totalBytes = 0;
        foreach (var node in root.Elements(Nfe + "loteDistDFeInt").Elements(Nfe + "docZip"))
        {
            if (status != 138 || documents.Count >= 50 ||
                ReadNsu((string?)node.Attribute("NSU")) is not long nsu || nsu == 0 || !seen.Add(nsu))
                throw new InvalidDataException("O lote da SEFAZ contém um docZip inesperado, inválido ou repetido.");

            string schema = ((string?)node.Attribute("schema") ?? "").Trim();
            if (schema.Length is 0 or > 120 || node.Value.Length > (MaxZip + 2) / 3 * 4 + 4096)
                throw new InvalidDataException("O docZip está sem schema ou excede o tamanho máximo.");

            byte[] xml = Unzip(node.Value, Math.Min(MaxXml, MaxBatchXml - totalBytes));
            ReadXml(xml, MaxXml);
            totalBytes += xml.Length;
            documents.Add(new(nsu, schema, xml));
        }
        if (status == 138 && documents.Count == 0)
            throw new InvalidDataException("A resposta 138 não contém documentos.");

        return new(status, Value(root, "xMotivo"), last, max, documents);
    }

    public static bool InWindow(DateTimeOffset instant, TimeOnly start, TimeOnly end)
    {
        if (start == end)
            throw new ArgumentException("O início e o fim da janela devem ser diferentes.");
        var time = TimeOnly.FromDateTime(instant.ToOffset(TimeSpan.FromHours(-3)).DateTime);
        return start < end ? time >= start && time < end : time >= start || time < end;
    }

    private static XElement? Single(IEnumerable<XElement> elements)
    {
        var candidates = elements.Take(2).ToArray();
        if (candidates.Length > 1)
            throw new InvalidDataException("A resposta da SEFAZ contém retornos duplicados.");
        return candidates.FirstOrDefault();
    }

    private static XElement ReadXml(byte[] xml, int limit)
    {
        if (xml.Length == 0 || xml.Length > limit)
            throw new InvalidDataException("O XML está vazio ou excede o tamanho máximo.");
        using var stream = new MemoryStream(xml, writable: false);
        using var reader = XmlReader.Create(stream, new XmlReaderSettings
        {
            DtdProcessing = DtdProcessing.Prohibit,
            XmlResolver = null,
            MaxCharactersInDocument = limit,
        });
        try { return XDocument.Load(reader).Root!; }
        catch (XmlException error) { throw new InvalidDataException("XML inválido no retorno da SEFAZ.", error); }
    }

    private static string Value(XElement node, string name) => node.Element(Nfe + name)?.Value.Trim() ?? "";

    private static long? ReadNsu(string? value) => value is { Length: > 0 and <= 15 }
        && value.All(char.IsAsciiDigit)
        ? long.Parse(value, CultureInfo.InvariantCulture)
        : null;

    private static byte[] Unzip(string base64, int limit)
    {
        byte[] zipped;
        try { zipped = Convert.FromBase64String(base64); }
        catch (FormatException error) { throw new InvalidDataException("O docZip contém base64 inválido.", error); }
        if (zipped.Length > MaxZip || zipped.Length < 18 || zipped[0] != 0x1f || zipped[1] != 0x8b)
            throw new InvalidDataException("O docZip está sem gzip ou excede o tamanho máximo.");

        using var source = new MemoryStream(zipped, writable: false);
        using var gzip = new GZipStream(source, CompressionMode.Decompress);
        using var result = new MemoryStream();
        byte[] buffer = new byte[32 * 1024];
        for (int size; (size = gzip.Read(buffer)) != 0;)
        {
            if (result.Length + size > limit)
                throw new InvalidDataException("O docZip expandido excede o tamanho máximo.");
            result.Write(buffer, 0, size);
        }
        return result.ToArray();
    }

    private static async Task<byte[]> ReadBoundedAsync(Stream stream, int limit, CancellationToken token)
    {
        using var result = new MemoryStream();
        byte[] buffer = new byte[32 * 1024];
        for (int size; (size = await stream.ReadAsync(buffer, token)) != 0;)
        {
            if (result.Length + size > limit)
                throw new InvalidDataException("A resposta da SEFAZ excede o tamanho máximo.");
            result.Write(buffer, 0, size);
        }
        return result.ToArray();
    }
}
