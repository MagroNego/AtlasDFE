using System.Text.Json;
using Atlas.Domain;
using Atlas.Infrastructure;
using ClosedXML.Excel;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http.Features;

internal static class FiscalEndpoints
{
    public static void MapFiscal(this WebApplication app, string connection, string cnpj)
    {
        app.MapPost("/api/aging", async (bool? previa, HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!(http.Request.Headers.ContainsKey("X-CSRF-TOKEN") && await csrf.IsRequestValidAsync(http)) || !http.Request.HasFormContentType)
                return AgingError("Envie um arquivo com uma sessão válida.");
            const int max = 5 * 1024 * 1024;
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = max + 65536;
            try
            {
                var form = await http.Request.ReadFormAsync(token);
                if (form.Files.Count != 1 || form.Files[0].Length is <= 0 or > max)
                    return AgingError("Envie um arquivo do Infor LN de até 5 MiB.");
                string extension = Path.GetExtension(form.Files[0].FileName).ToLowerInvariant();
                if (extension is not (".txt" or ".xlsx" or ".pdf"))
                    return AgingError("Envie um TXT, XLSX ou PDF do Infor LN.");
                using var buffer = new MemoryStream();
                await form.Files[0].CopyToAsync(buffer, token);
                var output = extension == ".pdf" ? AgingPdf.Convert(buffer.ToArray(), token)
                    : extension == ".xlsx" ? AgingTxt.ConvertLnWorkbook(buffer.ToArray(), token)
                    : AgingTxt.Convert(buffer.ToArray(), token);
                if (previa == true)
                {
                    using var workbook = new XLWorkbook(new MemoryStream(output, writable: false));
                    var sheet = workbook.Worksheet(1);
                    int columns = sheet.LastColumnUsed()?.ColumnNumber() ?? 0;
                    int rows = sheet.LastRowUsed()?.RowNumber() ?? 0;
                    return Results.Ok(new
                    {
                        aba = sheet.Name,
                        total = Math.Max(0, rows - 1),
                        colunas = Enumerable.Range(1, columns).Select(col => sheet.Cell(1, col).GetFormattedString()).ToArray(),
                        linhas = Enumerable.Range(2, Math.Min(10, Math.Max(0, rows - 1))).Select(row =>
                            Enumerable.Range(1, columns).Select(col => sheet.Cell(row, col).GetFormattedString()).ToArray()).ToArray(),
                    });
                }
                return Results.File(output,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "aging.xlsx");
            }
            catch (Exception error) when (error is InvalidDataException or BadHttpRequestException or DocumentFormat.OpenXml.Packaging.OpenXmlPackageException
                or IOException or FormatException or ArgumentException or OverflowException)
            {
                string message = error is InvalidDataException ? error.Message : "Arquivo de Aging inválido.";
                return AgingError(message);
            }
        }).RequireAuthorization(Administration.Export);

        var queue = new SefazQueue(connection);
        var admin = app.MapGroup("/api/admin").RequireAuthorization(policy => policy.RequireRole(Administration.Role));
        admin.MapAccounts(cnpj);
        admin.MapXmlHistory(connection, cnpj);
        var corrections = new ManualStatus(connection);
        admin.MapGet("/situacao", async (CancellationToken token) => Results.Ok(await corrections.HistoryAsync(cnpj, token)));
        admin.MapPost("/situacao", async (ManualInput input, HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada." });
            try
            {
                if (input.Notas is { } notes)
                    return Results.Ok(new { alteradas = await corrections.ApplyBatchAsync(cnpj, notes, input.Cancelada,
                        http.User.Identity?.Name ?? "", input.Motivo ?? "", token) });
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
            if (!await DownloadEndpoints.ValidCsrf(http, csrf))
                return DownloadEndpoints.SessionError();
            if (!http.Request.HasFormContentType)
                return Results.BadRequest(new { erro = "Envie os XMLs ou ZIPs como arquivos." });
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = EmissionStore.MaxBatch + 1024 * 1024;
            var streams = new List<(string Name, Stream Stream)>();
            try
            {
                var form = await http.Request.ReadFormAsync(token);
                if (form.Files.Count is < 1 or > 5000)
                    return Results.BadRequest(new { erro = "Envie de 1 a 5.000 arquivos." });
                foreach (var file in form.Files)
                    streams.Add((file.FileName, file.OpenReadStream()));
                var result = await store.ImportLoggedAsync(cnpj, streams, http.User.Identity!.Name!, token);
                var fileResult = new { result.Id, result.Notas, result.Cancelamentos, result.Cce, result.Duplicatas, result.Erros,
                    importadas = result.Notas + result.Cancelamentos + result.Cce, existentes = result.Duplicatas,
                    erro = result.Erros == 0 ? null : result.Registros.First(row => row.Estado == "erro").Mensagem };
                return result.Erros == 0 ? Results.Ok(fileResult) : Results.BadRequest(fileResult);
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

        group.MapGet("", async (string? busca, DateOnly? inicio, DateOnly? fim, string? situacao, string? item, int? pagina, CancellationToken token) =>
        {
            int page = pagina ?? 1;
            if (page is < 1 or > 1000 || !Valid(busca, inicio, fim, situacao) || (item?.Length ?? 0) > 200)
                return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
            var rows = await store.ListAsync(cnpj, busca?.Trim() ?? "", inicio, fim, situacao ?? "", (page - 1) * 100, 101, token, item?.Trim() ?? "");
            return Results.Ok(new { notas = rows.Take(100), mais = rows.Count > 100 });
        });

        group.MapGet("/{chave}/itens", async (string chave, CancellationToken token) =>
        {
            if (IdentificadorNota.Parse(chave)?.Chave != chave)
                return Results.BadRequest();
            string? items = await store.ItemsAsync(cnpj, chave, token);
            return items is null ? Results.NotFound() : Results.Content(items, "application/json");
        });

        group.MapEmissionReports(connection, cnpj);

    }

    internal static JsonElement MergeStatus(JsonElement details, bool manual) => manual
        ? JsonSerializer.SerializeToElement(new { cancelada = true, eventos = details.GetProperty("eventos"), ctes = details.GetProperty("ctes") })
        : details;

    private static IResult AgingError(string message) =>
        Results.BadRequest(new { erro = message, diagnostico = $"Atlas DFE - Aging List\r\n{message}\r\n" });

    private sealed record ManualInput(string? Nota, bool Cancelada, string? Motivo, string[]? Notas);
    private sealed record QueueInput(string? Nsu, bool Alta, bool Cancelar);

    private static bool Valid(string? query, DateOnly? start, DateOnly? end, string? status) =>
        (query?.Length ?? 0) <= 200 && !(start > end) && status is null or "" or "autorizada" or "cancelada";

    internal static IResult Excel(string title, string filename, string[] headers, IEnumerable<object[]> rows,
        CancellationToken token)
    {
        using var book = new XLWorkbook();
        WriteSheet(book, title, headers, rows, token);
        return DownloadExcel(book, filename);
    }

    internal static void WriteSheet(XLWorkbook book, string title, string[] headers, IEnumerable<object[]> rows,
        CancellationToken token)
    {
        var sheet = book.AddWorksheet(title);
        for (int col = 0; col < headers.Length; col++)
            sheet.Cell(1, col + 1).Value = headers[col];
        sheet.Cell(2, 1).InsertData(rows.Select(row =>
        {
            token.ThrowIfCancellationRequested();
            return row;
        }));
    }

    internal static IResult DownloadExcel(XLWorkbook book, string filename)
    {
        using var output = new MemoryStream();
        book.SaveAs(output);
        return Results.File(output.ToArray(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename);
    }
}
