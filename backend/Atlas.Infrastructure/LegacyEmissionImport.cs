using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using Npgsql;

namespace Atlas.Infrastructure;

internal static class LegacyEmissionImport
{
    public static async Task RunAsync(SqliteTransaction source, NpgsqlTransaction target, string cnpj, CancellationToken token)
    {
        using var notes = source.Connection!.CreateCommand();
        notes.Transaction = source;
        notes.CommandText = "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('xml_documentos','xml_relatorio')";
        if ((long)notes.ExecuteScalar()! != 2)
            return;

        await using (var clear = new NpgsqlCommand("DELETE FROM emissao_notas WHERE cnpj=$1", target.Connection, target))
        {
            clear.Parameters.AddWithValue(cnpj);
            await clear.ExecuteNonQueryAsync(token);
        }

        notes.CommandText = """
            SELECT chave,numero,serie,emissao,emitente,destinatario,valor,sha256 FROM xml_documentos WHERE cnpj=$cnpj
            """;
        notes.Parameters.AddWithValue("$cnpj", cnpj);
        using var rows = notes.ExecuteReader();
        using var items = source.Connection.CreateCommand();
        items.Transaction = source;
        items.CommandText = """
            SELECT tipo,dados FROM xml_relatorio WHERE cnpj=$cnpj AND chave=$chave AND tipo IN ('itens','retencoes') ORDER BY linha
            """;
        items.Parameters.AddWithValue("$cnpj", cnpj);
        var key = items.Parameters.Add("$chave", SqliteType.Text);
        while (rows.Read())
        {
            key.Value = rows.GetString(0);
            var details = new List<JsonElement>();
            decimal pis = 0, cofins = 0;
            using (var reader = items.ExecuteReader())
                while (reader.Read())
                {
                    using var item = JsonDocument.Parse(reader.GetString(1));
                    if (reader.GetString(0) == "itens")
                        details.Add(item.RootElement.Clone());
                    else
                    {
                        pis += Retained(item.RootElement, "Vl. PIS Retido");
                        cofins += Retained(item.RootElement, "Vl. COFINS Retido");
                    }
                }
            var note = new EmissionNote(rows.GetString(0),
                int.Parse(rows.GetString(1), CultureInfo.InvariantCulture), int.Parse(rows.GetString(2), CultureInfo.InvariantCulture),
                DateOnly.ParseExact(rows.GetString(3), "yyyy-MM-dd", CultureInfo.InvariantCulture),
                rows.GetString(4), rows.GetString(5), EmissionStore.Amount(rows.GetString(6)),
                JsonSerializer.SerializeToElement(details), rows.GetString(7), pis, cofins);
            await EmissionStore.InsertAsync(target, cnpj, note, token);
        }
    }

    private static decimal Retained(JsonElement row, string label)
    {
        if (!row.TryGetProperty(label, out var value))
            return 0;
        if (value.ValueKind == JsonValueKind.Number)
            return value.GetDecimal();
        string text = value.GetString() ?? "";
        if (text.Contains(','))
            text = text.Replace(".", "").Replace(',', '.');
        return EmissionStore.AmountOrZero(text);
    }
}
