using System.Globalization;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Xml.Linq;
using Atlas.Domain;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

public sealed record EmissionNote(string Chave, int Numero, int Serie, DateOnly Emissao,
    string Emitente, string Destinatario, decimal Valor, JsonElement Itens, string Hash,
    decimal PisRetido = 0, decimal CofinsRetido = 0);

public sealed record EmissionRow(string Chave, int Numero, int Serie, DateOnly Emissao,
    string Emitente, string Destinatario, decimal Valor, int Itens, bool Cancelada, decimal PisRetido, decimal CofinsRetido);

public sealed class EmissionStore(string connection)
{
    public const int MaxXml = 8 * 1024 * 1024;
    public const int MaxBatch = 64 * 1024 * 1024;
    private static readonly XNamespace Nfe = "http://www.portalfiscal.inf.br/nfe";

    public async Task<(int Imported, int Existing)> ImportAsync(
        string cnpj, IEnumerable<(string Name, Stream Stream)> files, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = new NpgsqlCommand("SELECT pg_advisory_xact_lock(718320, hashtext($1))", db, tx))
        {
            guard.Parameters.AddWithValue(cnpj);
            await guard.ExecuteNonQueryAsync(token);
        }
        int imported = 0, existing = 0, expanded = 0, documents = 0;
        foreach (var (name, stream) in files)
        {
            if (Path.GetExtension(name).Equals(".zip", StringComparison.OrdinalIgnoreCase))
            {
                using var zip = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: true);
                foreach (var entry in zip.Entries)
                {
                    if (!Path.GetExtension(entry.Name).Equals(".xml", StringComparison.OrdinalIgnoreCase))
                        continue;
                    await using var input = entry.Open();
                    await Save(input);
                }
            }
            else if (Path.GetExtension(name).Equals(".xml", StringComparison.OrdinalIgnoreCase))
                await Save(stream);
            else
                throw new InvalidDataException("Envie arquivos XML ou ZIP.");
        }
        if (documents == 0)
            throw new InvalidDataException("Nenhum XML foi encontrado.");
        await tx.CommitAsync(token);
        return (imported, existing);

        async Task Save(Stream stream)
        {
            if (++documents > 5000)
                throw new InvalidDataException("O lote contém mais de 5.000 XMLs.");
            using var buffer = new MemoryStream();
            byte[] chunk = new byte[32 * 1024];
            for (int size; (size = await stream.ReadAsync(chunk, token)) > 0;)
            {
                if (buffer.Length + size > MaxXml || (expanded += size) > MaxBatch)
                    throw new InvalidDataException("O tamanho do XML ou do lote excede o limite.");
                buffer.Write(chunk, 0, size);
            }
            byte[] xml = buffer.ToArray();
            var root = SefazDistribution.ReadXml(xml, MaxXml);
            if (root.Name == Nfe + "procEventoNFe")
            {
                await SefazConsulta.PublishAsync(db, tx, cnpj, new SefazDocument(0, "importacao", xml), token,
                    imported: true);
                imported++;
                return;
            }
            if (await InsertAsync(tx, cnpj, Parse(cnpj, xml), token))
                imported++;
            else
                existing++;
        }
    }

    public static EmissionNote Parse(string cnpj, byte[] xml)
    {
        var root = SefazDistribution.ReadXml(xml, MaxXml);
        var nfe = root.Name == Nfe + "nfeProc" ? root.Element(Nfe + "NFe") : root;
        var info = nfe?.Name == Nfe + "NFe" ? nfe.Element(Nfe + "infNFe") : null;
        string id = (string?)info?.Attribute("Id") ?? "";
        string key = id.StartsWith("NFe", StringComparison.Ordinal) ? id[3..] : "";
        if (key.Length != 44 || IdentificadorNota.Parse(key)?.Chave != key)
            throw new InvalidDataException("Chave de NF-e modelo 55 inválida.");

        var emit = info!.Element(Nfe + "emit");
        if (key[6..20] != cnpj || Value(emit, "CNPJ") != cnpj)
            throw new InvalidDataException("O XML pertence a outro emitente.");
        var ide = info.Element(Nfe + "ide");
        if (!int.TryParse(Value(ide, "nNF"), NumberStyles.None, CultureInfo.InvariantCulture, out int number)
            || !int.TryParse(Value(ide, "serie"), NumberStyles.None, CultureInfo.InvariantCulture, out int series)
            || number != int.Parse(key.AsSpan(25, 9), CultureInfo.InvariantCulture)
            || series != int.Parse(key.AsSpan(22, 3), CultureInfo.InvariantCulture))
            throw new InvalidDataException("O número ou a série diverge da chave da NF-e.");

        string date = First(Value(ide, "dhEmi"), Value(ide, "dEmi"));
        if (date.Length < 10 || !DateOnly.TryParseExact(date[..10], "yyyy-MM-dd",
            CultureInfo.InvariantCulture, DateTimeStyles.None, out var emission))
            throw new InvalidDataException("Data de emissão inválida.");
        decimal amount = Amount(Value(info.Element(Nfe + "total")?.Element(Nfe + "ICMSTot"), "vNF"));
        var items = info.Elements(Nfe + "det").Take(991).Select(item =>
        {
            var product = item.Element(Nfe + "prod") ?? throw new InvalidDataException("Item sem produto.");
            var fields = new Dictionary<string, object>
            {
                ["Item"] = (string?)item.Attribute("nItem") ?? "",
                ["Codigo"] = Value(product, "cProd"), ["Descricao"] = Value(product, "xProd"),
                ["NCM"] = Value(product, "NCM"), ["CFOP"] = Value(product, "CFOP"),
                ["Unidade"] = Value(product, "uCom"), ["Qtd"] = Amount(Value(product, "qCom")),
                ["Vl. Unitario"] = Amount(Value(product, "vUnCom")), ["Vl. Total"] = Amount(Value(product, "vProd")),
            };
            foreach (string tax in new[] { "ICMS", "IPI", "PIS", "COFINS" })
            {
                var taxed = item.Element(Nfe + "imposto")?.Element(Nfe + tax)?.Elements()
                    .FirstOrDefault(node => node.Element(Nfe + "CST") is not null || node.Element(Nfe + "CSOSN") is not null);
                fields[$"CST {tax}"] = Value(taxed, "CST");
                fields[$"Base {tax}"] = OptionalAmount(Value(taxed, "vBC"));
                fields[$"% {tax}"] = OptionalAmount(Value(taxed, $"p{tax}"));
                fields[$"Vl. {tax}"] = OptionalAmount(Value(taxed, $"v{tax}"));
                if (tax == "ICMS")
                {
                    fields["CSOSN"] = Value(taxed, "CSOSN");
                    fields["Origem ICMS"] = Value(taxed, "orig");
                }
                if (tax is "PIS" or "COFINS")
                {
                    fields[$"Qtd Base {tax}"] = OptionalAmount(Value(taxed, "qBCProd"));
                    fields[$"Aliquota {tax} por unidade"] = OptionalAmount(Value(taxed, "vAliqProd"));
                }
            }
            return fields;
        }).ToArray();
        if (items.Length is 0 or > 990)
            throw new InvalidDataException("A NF-e deve ter entre 1 e 990 itens.");
        var withheld = info.Element(Nfe + "total")?.Element(Nfe + "retTrib");
        return new(key, number, series, emission, Value(emit, "xNome"),
            Value(info.Element(Nfe + "dest"), "xNome"), amount, JsonSerializer.SerializeToElement(items),
            Convert.ToHexString(SHA256.HashData(xml)),
            AmountOrZero(Value(withheld, "vRetPIS")), AmountOrZero(Value(withheld, "vRetCOFINS")));
    }

    internal static async Task<bool> InsertAsync(NpgsqlTransaction tx, string cnpj, EmissionNote note, CancellationToken token)
    {
        await using var command = new NpgsqlCommand("""
            INSERT INTO emissao_notas(cnpj,chave,numero,serie,emissao,emitente,destinatario,valor,itens,hash,pis_retido,cofins_retido)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) ON CONFLICT (cnpj,chave) DO NOTHING
            """, tx.Connection, tx);
        foreach (var value in new object[] { cnpj, note.Chave, note.Numero, note.Serie, note.Emissao,
            note.Emitente, note.Destinatario, note.Valor })
            command.Parameters.AddWithValue(value);
        command.Parameters.AddWithValue(NpgsqlDbType.Jsonb, note.Itens.GetRawText());
        command.Parameters.AddWithValue(note.Hash);
        command.Parameters.AddWithValue(note.PisRetido);
        command.Parameters.AddWithValue(note.CofinsRetido);
        return await command.ExecuteNonQueryAsync(token) == 1;
    }

    public async Task<List<EmissionRow>> ListAsync(string cnpj, string query, DateOnly? start, DateOnly? end,
        string status, int skip, int limit, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("""
            SELECT e.chave,e.numero,e.serie,e.emissao,e.emitente,e.destinatario,e.valor,jsonb_array_length(e.itens),
                (coalesce((n.detalhe->>'cancelada')::boolean,false) OR coalesce(m.cancelada,false)) AS cancelada,e.pis_retido,e.cofins_retido
            FROM emissao_notas e LEFT JOIN consulta_notas n USING (cnpj,chave)
            LEFT JOIN atlas_situacao_atual m USING (cnpj,chave)
            WHERE e.cnpj=$1 AND ($2='' OR strpos(lower(concat_ws(' ',e.chave,e.numero,e.emitente,e.destinatario)),lower($2))>0
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(e.itens) AS i
                    WHERE strpos(lower(concat_ws(' ',i->>'Codigo',i->>'Descricao',i->>'NCM')),lower($2))>0))
                AND ($3 IS NULL OR e.emissao >= $3) AND ($4 IS NULL OR e.emissao <= $4)
                AND ($5='' OR (coalesce((n.detalhe->>'cancelada')::boolean,false) OR coalesce(m.cancelada,false))=($5='cancelada'))
            ORDER BY e.emissao DESC,e.chave LIMIT $6 OFFSET $7
            """, db);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(query);
        command.Parameters.Add(new() { NpgsqlDbType = NpgsqlDbType.Date, Value = (object?)start ?? DBNull.Value });
        command.Parameters.Add(new() { NpgsqlDbType = NpgsqlDbType.Date, Value = (object?)end ?? DBNull.Value });
        command.Parameters.AddWithValue(status);
        command.Parameters.AddWithValue(limit);
        command.Parameters.AddWithValue(skip);
        var result = new List<EmissionRow>();
        await using var rows = await command.ExecuteReaderAsync(token);
        while (await rows.ReadAsync(token))
            result.Add(new(rows.GetString(0), rows.GetInt32(1), rows.GetInt32(2), rows.GetFieldValue<DateOnly>(3),
                rows.GetString(4), rows.GetString(5), rows.GetDecimal(6), rows.GetInt32(7), rows.GetBoolean(8), rows.GetDecimal(9), rows.GetDecimal(10)));
        return result;
    }

    public async Task<string?> ItemsAsync(string cnpj, string key, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("SELECT itens::text FROM emissao_notas WHERE cnpj=$1 AND chave=$2", db);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(key);
        return (string?)await command.ExecuteScalarAsync(token);
    }

    private static string Value(XElement? element, string name) => element?.Element(Nfe + name)?.Value.Trim() ?? "";
    private static string First(string value, string fallback) => value.Length > 0 ? value : fallback;
    private static object OptionalAmount(string value) => value.Length == 0 ? "" : Amount(value);

    internal static decimal AmountOrZero(string value) => value.Length == 0 ? 0 : Amount(value);

    internal static decimal Amount(string value) =>
        decimal.TryParse(value, NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out var amount)
            ? amount : throw new InvalidDataException("Valor ou quantidade inválida.");
}
