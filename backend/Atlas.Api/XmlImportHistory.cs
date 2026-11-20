using System.Globalization;
using System.Text;
using System.Text.Json;
using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Npgsql;

internal static class XmlImportHistory
{
    public static void MapXmlHistory(this RouteGroupBuilder admin, string connection, string cnpj)
    {
        admin.MapGet("/importacoes", async (CancellationToken token) =>
        {
            await using var db = new NpgsqlConnection(connection);
            await db.OpenAsync(token);
            await using var command = new NpgsqlCommand("""
                SELECT id,usuario,criado_em, (resultado-'Registros'-'Id')::text
                FROM atlas_importacao_xml WHERE cnpj=$1 ORDER BY id DESC LIMIT 50
                """, db);
            command.Parameters.AddWithValue(cnpj);
            await using var rows = await command.ExecuteReaderAsync(token);
            var batches = new List<object>();
            while (await rows.ReadAsync(token))
            {
                using var data = JsonDocument.Parse(rows.GetString(3));
                var result = data.RootElement;
                batches.Add(new { id = rows.GetInt64(0), usuario = rows.GetString(1), criadoEm = rows.GetDateTime(2),
                    notas = Count("Notas"), cancelamentos = Count("Cancelamentos"), cce = Count("Cce"),
                    duplicatas = Count("Duplicatas"), erros = Count("Erros") });
                int Count(string field) => result.GetProperty(field).GetInt32();
            }
            return Results.Ok(batches);
        });
        admin.MapPost("/importacoes/{id:long}/relatorio", async (long id, HttpContext http,
            IAntiforgery csrf, DownloadQueue queue, CancellationToken token) =>
        {
            if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
            return await DownloadEndpoints.EnqueueAsync(http, queue, new("importacao", Importacao: id), token);
        });
    }

    public static async Task<IResult> ExportAsync(string connection, string cnpj, long id, CancellationToken token,
        Func<string, CancellationToken, Task> progress)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("SELECT resultado::text FROM atlas_importacao_xml WHERE cnpj=$1 AND id=$2", db);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(id);
        if (await command.ExecuteScalarAsync(token) is not string data)
            return Results.NotFound(new { erro = "Lote de importação não encontrado." });
        var result = JsonSerializer.Deserialize<XmlImportResult>(data)!;
        await progress("gerando", token);
        var output = new StringBuilder("Arquivo;Situação;Mensagem\r\n");
        foreach (var record in result.Registros)
        {
            token.ThrowIfCancellationRequested();
            output.Append(Csv(record.Arquivo)).Append(';').Append(Csv(record.Estado)).Append(';')
                .Append(Csv(record.Mensagem)).Append("\r\n");
        }
        string name = "importacao_" + id.ToString(CultureInfo.InvariantCulture) + ".csv";
        return Results.File(Encoding.UTF8.GetPreamble().Concat(Encoding.UTF8.GetBytes(output.ToString())).ToArray(),
            "text/csv; charset=utf-8", name);
    }

    private static string Csv(string value)
    {
        if (value.TrimStart() is ['=' or '+' or '-' or '@', ..]) value = "'" + value;
        return "\"" + value.Replace("\"", "\"\"") + "\"";
    }
}
