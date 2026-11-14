using System.Text.Json;
using Atlas.Domain;
using Atlas.Infrastructure;
using ClosedXML.Excel;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.EntityFrameworkCore;

internal static class FiscalEndpoints
{
    public static void MapFiscal(this WebApplication app, string connection, string cnpj)
    {
        app.MapPost("/api/aging", async (HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http) || !http.Request.HasFormContentType)
                return Results.BadRequest(new { erro = "Envie um arquivo com uma sessão válida." });
            const int max = 5 * 1024 * 1024;
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = max + 65536;
            try
            {
                var form = await http.Request.ReadFormAsync(token);
                if (form.Files.Count != 1 || form.Files[0].Length is <= 0 or > max)
                    return Results.BadRequest(new { erro = "Envie um arquivo do Infor LN de até 5 MiB." });
                string extension = Path.GetExtension(form.Files[0].FileName).ToLowerInvariant();
                if (extension is not (".txt" or ".xlsx" or ".pdf"))
                    return Results.BadRequest(new { erro = "Envie um TXT, XLSX ou PDF do Infor LN." });
                using var buffer = new MemoryStream();
                await form.Files[0].CopyToAsync(buffer, token);
                var output = extension == ".pdf" ? AgingPdf.Convert(buffer.ToArray(), token)
                    : extension == ".xlsx" ? AgingTxt.ConvertLnWorkbook(buffer.ToArray(), token)
                    : AgingTxt.Convert(buffer.ToArray(), token);
                return Results.File(output,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "aging.xlsx");
            }
            catch (Exception error) when (error is InvalidDataException or BadHttpRequestException or DocumentFormat.OpenXml.Packaging.OpenXmlPackageException)
            {
                return Results.BadRequest(new { erro = error is InvalidDataException ? error.Message : "Arquivo de Aging inválido." });
            }
        }).RequireAuthorization();

        var queue = new SefazQueue(connection);
        var admin = app.MapGroup("/api/admin").RequireAuthorization(policy => policy.RequireRole(Administration.Role));
        admin.MapAccounts(cnpj);
        var corrections = new ManualStatus(connection);
        admin.MapGet("/situacao", async (CancellationToken token) => Results.Ok(await corrections.HistoryAsync(cnpj, token)));
        admin.MapPost("/situacao", async (ManualInput input, HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada." });
            try
            {
                await corrections.ApplyAsync(cnpj, input.Nota ?? "", input.Cancelada,
                    http.User.Identity?.Name ?? "", input.Motivo ?? "", token);
                return Results.NoContent();
            }
            catch (InvalidDataException error)
            {
                return Results.BadRequest(new { erro = error.Message });
            }
        });
        admin.MapGet("/fila-nsu", async (CancellationToken token) => Results.Ok(await queue.ListAsync(cnpj, token)));
        admin.MapPost("/fila-nsu", async (QueueInput input, HttpContext http, IAntiforgery csrf,
            CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada." });
            if (input.Nsu is not { Length: > 0 and <= 15 } value || !value.All(char.IsAsciiDigit)
                || !long.TryParse(value, out long nsu) || nsu == 0)
                return Results.BadRequest(new { erro = "Informe um NSU válido." });
            try
            {
                if (input.Cancelar)
                    return await queue.CancelAsync(cnpj, nsu, token) ? Results.NoContent() : Results.NotFound();
                await queue.EnqueueAsync(cnpj, nsu, input.Alta, token);
                return Results.NoContent();
            }
            catch (InvalidDataException error)
            {
                return Results.BadRequest(new { erro = error.Message });
            }
        });

        var store = new EmissionStore(connection);
        var group = app.MapGroup("/api/emissao").RequireAuthorization();
        group.MapPost("/xml", async (HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada. Atualize a página." });
            if (!http.Request.HasFormContentType)
                return Results.BadRequest(new { erro = "Envie os XMLs ou ZIPs como arquivos." });
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = EmissionStore.MaxBatch + 1024 * 1024;
            var streams = new List<(string Name, Stream Stream)>();
            try
            {
                var form = await http.Request.ReadFormAsync(token);
                foreach (var file in form.Files)
                    streams.Add((file.FileName, file.OpenReadStream()));
                var result = await store.ImportAsync(cnpj, streams, token);
                return Results.Ok(new { importadas = result.Imported, existentes = result.Existing });
            }
            catch (Exception error) when (error is InvalidDataException or BadHttpRequestException)
            {
                return Results.BadRequest(new { erro = error.Message });
            }
            finally
            {
                foreach (var (_, stream) in streams)
                    await stream.DisposeAsync();
            }
        }).RequireAuthorization(policy => policy.RequireRole(Administration.Role));

        group.MapGet("", async (string? busca, DateOnly? inicio, DateOnly? fim, string? situacao, int? pagina, CancellationToken token) =>
        {
            int page = pagina ?? 1;
            if (page is < 1 or > 1000 || !Valid(busca, inicio, fim, situacao))
                return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
            var rows = await store.ListAsync(cnpj, busca?.Trim() ?? "", inicio, fim, situacao ?? "", (page - 1) * 100, 101, token);
            return Results.Ok(new { notas = rows.Take(100), mais = rows.Count > 100 });
        });

        group.MapGet("/{chave}/itens", async (string chave, CancellationToken token) =>
        {
            if (IdentificadorNota.Parse(chave)?.Chave != chave)
                return Results.BadRequest();
            string? items = await store.ItemsAsync(cnpj, chave, token);
            return items is null ? Results.NotFound() : Results.Content(items, "application/json");
        });

        group.MapGet("/{chave}/itens/excel", async (string chave, CancellationToken token) =>
        {
            if (IdentificadorNota.Parse(chave)?.Chave != chave)
                return Results.BadRequest();
            string? items = await store.ItemsAsync(cnpj, chave, token);
            if (items is null)
                return Results.NotFound();
            using var content = JsonDocument.Parse(items);
            var fields = content.RootElement.EnumerateArray()
                .SelectMany(item => item.EnumerateObject().Select(field => field.Name))
                .Distinct(StringComparer.Ordinal).ToArray();
            if (fields.Length == 0)
                return Results.NotFound();
            var rows = content.RootElement.EnumerateArray().Select(item => fields.Select(field =>
            {
                if (!item.TryGetProperty(field, out var value))
                    return "";
                return value.ValueKind switch
                {
                    JsonValueKind.Number when value.TryGetDecimal(out var number) => (object)number,
                    JsonValueKind.String => value.GetString() ?? "",
                    _ => "",
                };
            }).ToArray()).ToArray();
            return Excel("Itens", $"itens_{chave}.xlsx", fields, rows, token);
        });

        group.MapGet("/excel", async (string? busca, DateOnly? inicio, DateOnly? fim, string? situacao, CancellationToken token) =>
        {
            if (!Valid(busca, inicio, fim, situacao))
                return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
            var rows = await store.ListAsync(cnpj, busca?.Trim() ?? "", inicio, fim, situacao ?? "", 0, 100001, token);
            if (rows.Count > 100000)
                return Results.BadRequest(new { erro = "Mais de 100.000 notas. Refine os filtros." });
            return Excel("Emissão", "emissao.xlsx",
                ["Chave Acesso", "Número Nota", "Série", "Data Emissão", "Emitente", "Destinatário", "Valor Nota", "Itens", "Situação", "PIS Retido", "COFINS Retido"],
                rows.Select(n => new object[] { n.Chave, n.Numero, n.Serie, n.Emissao.ToDateTime(TimeOnly.MinValue),
                    n.Emitente, n.Destinatario, n.Valor, n.Itens, n.Cancelada ? "Cancelada" : "Autorizada", n.PisRetido, n.CofinsRetido }), token);
        });

        app.MapGet("/api/consulta/excel", async (string? busca, AtlasDbContext db, CancellationToken token) =>
        {
            var id = IdentificadorNota.Parse(busca);
            if (id is null)
                return Results.BadRequest(new { erro = "Informe o número ou a chave da NF-e." });
            var query = db.Notas.AsNoTracking().Where(note => note.Cnpj == cnpj);
            query = id.Chave is not null ? query.Where(note => note.Chave == id.Chave)
                : query.Where(note => note.Numero == id.Numero);
            var notes = await query.OrderBy(note => note.Chave).Take(1001)
                .Select(note => new { note.Chave, note.Numero, note.Serie, note.Detalhe,
                    Manual = db.Situacoes.Any(s => s.Cnpj == note.Cnpj && s.Chave == note.Chave && s.Cancelada) })
                .ToListAsync(token);
            if (notes.Count > 1000)
                return Results.BadRequest(new { erro = "Use a chave para restringir a busca." });
            var rows = new List<object[]>();
            foreach (var note in notes)
            {
                bool canceled = note.Manual || note.Detalhe.GetProperty("cancelada").GetBoolean();
                string status = canceled ? "Cancelada" : "Autorizada";
                var events = note.Detalhe.GetProperty("eventos");
                if (events.GetArrayLength() == 0)
                    rows.Add([note.Chave, note.Numero, note.Serie, status, "", "", "", "", ""]);
                foreach (var e in events.EnumerateArray())
                {
                    if (rows.Count >= 100000)
                        return Results.BadRequest(new { erro = "Mais de 100.000 eventos. Informe a chave da nota." });
                    rows.Add([note.Chave, note.Numero, note.Serie, status, Value(e, "descricao"),
                        Value(e, "data"), Value(e, "protocolo"), Value(e, "nsu"), Value(e, "correcao")]);
                }
            }
            return Excel("Manifestação", "manifestacao.xlsx",
                ["Chave Acesso", "Número Nota", "Série", "Situação", "Evento", "Data", "Protocolo", "NSU", "Correção"], rows, token);
        }).RequireAuthorization();
    }

    internal static JsonElement MergeStatus(JsonElement details, bool manual) => manual
        ? JsonSerializer.SerializeToElement(new { cancelada = true, eventos = details.GetProperty("eventos"), ctes = details.GetProperty("ctes") })
        : details;

    private sealed record ManualInput(string? Nota, bool Cancelada, string? Motivo);
    private sealed record QueueInput(string? Nsu, bool Alta, bool Cancelar);

    private static bool Valid(string? query, DateOnly? start, DateOnly? end, string? status) =>
        (query?.Length ?? 0) <= 200 && !(start > end) && status is null or "" or "autorizada" or "cancelada";

    private static string Value(JsonElement element, string field) =>
        element.TryGetProperty(field, out var value) ? value.GetString() ?? "" : "";

    private static IResult Excel(string title, string filename, string[] headers, IEnumerable<object[]> rows,
        CancellationToken token)
    {
        using var book = new XLWorkbook();
        var sheet = book.AddWorksheet(title);
        for (int col = 0; col < headers.Length; col++)
            sheet.Cell(1, col + 1).Value = headers[col];
        var heading = sheet.Range(1, 1, 1, headers.Length);
        heading.Style.Font.Bold = true;
        heading.Style.Fill.BackgroundColor = XLColor.FromHtml("#DCEAE1");
        sheet.SheetView.FreezeRows(1);
        sheet.Columns().Width = 20;
        sheet.Column(1).Width = 48;
        sheet.Cell(2, 1).InsertData(rows.Select(row =>
        {
            token.ThrowIfCancellationRequested();
            return row;
        }));
        using var output = new MemoryStream();
        book.SaveAs(output);
        return Results.File(output.ToArray(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename);
    }
}
