using System.Globalization;
using System.Text;
using Atlas.Domain;
using Atlas.Infrastructure;
using ClosedXML.Excel;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Mvc;

internal static class EmissionReportEndpoints
{
    private static readonly HashSet<string> ExcelExcluded = ["CSOSN", "Origem ICMS", "Qtd Base PIS",
        "Aliquota PIS por unidade", "Qtd Base COFINS", "Aliquota COFINS por unidade"];

    public static void MapEmissionReports(this RouteGroupBuilder group, string connection, string cnpj)
    {
        group.MapGet("/relatorio", async ([AsParameters] Filters filters, int? pagina, CancellationToken token) =>
        {
            int page = pagina ?? 1;
            if (!Valid(filters) || page is < 1 or > 1000)
                return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
            var report = await Read(new(connection), cnpj, filters, "", (page - 1) * 100, 101, token);
            // Mantém série disponível nos arquivos, sem reintroduzi-la na tabela.
            return Results.Ok(new { colunas = report.Colunas.Where(field => field != "Serie"),
                linhas = report.Linhas.Take(100), notas = Array.Empty<object>(), mais = report.Linhas.Count > 100 });
        });

        group.MapGet("/excel", ([AsParameters] Filters filters, CancellationToken token) => ExportAsync(connection, cnpj, filters, "", false, token)).RequireAuthorization(Administration.Export);
        group.MapGet("/csv", ([AsParameters] Filters filters, CancellationToken token) => ExportAsync(connection, cnpj, filters, "", true, token)).RequireAuthorization(Administration.Export);
        group.MapGet("/{chave}/itens/excel", (string chave, [AsParameters] Filters filters, CancellationToken token) =>
            ExportAsync(connection, cnpj, filters with { Tipo = "itens" }, chave, false, token)).RequireAuthorization(Administration.Export);
        group.MapGet("/{chave}/itens/csv", (string chave, [AsParameters] Filters filters, CancellationToken token) =>
            ExportAsync(connection, cnpj, filters with { Tipo = "itens" }, chave, true, token)).RequireAuthorization(Administration.Export);

        foreach (bool csv in new[] { false, true })
        {
            string extension = csv ? "csv" : "excel";
            group.MapPost($"/{extension}", ([AsParameters] Filters filters, HttpContext http, IAntiforgery csrf,
                DownloadQueue queue, CancellationToken token) => Enqueue(filters, "", csv, http, csrf, queue, token)).RequireAuthorization(Administration.Export);
            group.MapPost($"/{{chave}}/itens/{extension}", (string chave, [AsParameters] Filters filters, HttpContext http,
                IAntiforgery csrf, DownloadQueue queue, CancellationToken token) =>
                Enqueue(filters with { Tipo = "itens" }, chave, csv, http, csrf, queue, token)).RequireAuthorization(Administration.Export);
        }
    }

    private static async Task<IResult> Enqueue(Filters filters, string key, bool csv, HttpContext http,
        IAntiforgery csrf, DownloadQueue queue, CancellationToken token)
    {
        if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
        if (!Valid(filters, key)) return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
        return await DownloadEndpoints.EnqueueAsync(http, queue,
            new(key.Length > 0 ? "itens" : "emissao", Emissao: filters, Chave: key, Csv: csv), token);
    }

    private static Task<EmissionReport> Read(EmissionReports store, string cnpj, Filters f, string key,
        int skip, int limit, CancellationToken token) =>
        store.ReadAsync(cnpj, f.Tipo ?? "notas", f.Busca?.Trim() ?? "", f.Inicio, f.Fim,
            f.Situacao ?? "", f.Item?.Trim() ?? "", key, skip, limit, token);

    internal static async Task<IResult> ExportAsync(string connection, string cnpj, Filters f, string key,
        bool csv, CancellationToken token, Func<string, CancellationToken, Task>? progress = null)
    {
        if (!Valid(f, key))
            return Results.BadRequest(new { erro = "Filtros de emissão inválidos." });
        var store = new EmissionReports(connection);
        try
        {
            if (key.Length > 0 && await new EmissionStore(connection).ItemsAsync(cnpj, key, token) is null)
                return Results.NotFound(new { erro = "NF-e não encontrada." });
            if (csv || key.Length > 0)
            {
                var report = await Read(store, cnpj, f, key, 0, EmissionReports.MaxRows + 1, token);
                EmissionReports.EnsureLimit(report);
                if (progress is not null) await progress("gerando", token);
                string filename = FileName([report], f, csv ? "csv" : "xlsx", key);
                if (csv) return Csv(report, filename, token);
                using var book = new XLWorkbook();
                Sheet(book, "Itens", report, token);
                return FiscalEndpoints.DownloadExcel(book, filename);
            }
            var reports = await store.WorkbookAsync(cnpj, f.Busca?.Trim() ?? "", f.Inicio, f.Fim,
                f.Situacao ?? "", f.Item?.Trim() ?? "", token);
            if (progress is not null) await progress("gerando", token);
            using var workbook = new XLWorkbook();
            string[] titles = ["Notas", "Itens", "Retencoes", "Resumo"];
            for (int index = 0; index < reports.Length; index++)
                Sheet(workbook, titles[index], reports[index], token);
            return FiscalEndpoints.DownloadExcel(workbook, FileName(reports, f, "xlsx", ""));
        }
        catch (InvalidDataException error)
        {
            return Results.BadRequest(new { erro = error.Message });
        }
    }

    private static bool Valid(Filters f, string key = "") => (f.Busca?.Length ?? 0) <= 200 && (f.Item?.Length ?? 0) <= 200
        && !(f.Inicio > f.Fim) && f.Situacao is null or "" or "autorizada" or "cancelada"
        && f.Tipo is null or "notas" or "itens" or "retencoes"
        && (key.Length == 0 || IdentificadorNota.Parse(key)?.Chave == key);

    private static void Sheet(XLWorkbook workbook, string title, EmissionReport report, CancellationToken token)
    {
        var columns = report.Colunas.Where(field => !ExcelExcluded.Contains(field)).ToArray();
        FiscalEndpoints.WriteSheet(workbook, title, columns, report.Linhas.Select(row => columns.Select(field =>
        {
            object value = row.GetValueOrDefault(field, "");
            if (field == "CT-e vinculado") return Equals(value, "Não localizado") ? value : "CT-e Vinculado";
            if (field is "Data Emissao" or "Data" && value is string date && DateOnly.TryParseExact(date,
                ["yyyy-MM-dd", "dd/MM/yyyy"], CultureInfo.InvariantCulture, DateTimeStyles.None, out var day))
                return day.ToDateTime(TimeOnly.MinValue);
            return EmissionReports.Cell(field, value);
        }).ToArray()), token);
    }

    private static IResult Csv(EmissionReport report, string filename, CancellationToken token)
    {
        var output = new StringBuilder();
        static string Quote(object value)
        {
            string text = Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
            string trimmed = text.TrimStart();
            if (value is string && (trimmed.StartsWith('=') || trimmed.StartsWith('+')
                || trimmed.StartsWith('-') || trimmed.StartsWith('@'))) text = "'" + text;
            return "\"" + text.Replace("\"", "\"\"") + "\"";
        }
        output.AppendLine(string.Join(';', report.Colunas.Select(Quote)));
        foreach (var row in report.Linhas)
        {
            token.ThrowIfCancellationRequested();
            output.AppendLine(string.Join(';', report.Colunas.Select(field => Quote(row.GetValueOrDefault(field, "")))));
        }
        return Results.File(Encoding.UTF8.GetPreamble().Concat(Encoding.UTF8.GetBytes(output.ToString())).ToArray(),
            "text/csv; charset=utf-8", filename);
    }

    private static string FileName(IEnumerable<EmissionReport> reports, Filters f, string extension, string key)
    {
        var dates = reports.SelectMany(report => report.Linhas).Select(row => row.GetValueOrDefault("Data Emissao", "").ToString())
            .Select(value => DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var day)
                ? (DateOnly?)day : null).Where(day => day.HasValue).Select(day => day!.Value).ToArray();
        DateOnly? start = f.Inicio ?? (dates.Length == 0 ? null : dates.Min());
        DateOnly? end = f.Fim ?? (dates.Length == 0 ? null : dates.Max());
        string period = start.HasValue || end.HasValue
            ? $"{start?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? "inicio"}-{end?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? "fim"}"
            : "sem_periodo";
        string kind = key.Length > 0 ? $"itens_{key}" : extension == "csv" ? $"emissao_{f.Tipo ?? "notas"}" : "emissao";
        return $"{kind}_{period}.{extension}";
    }

    internal sealed record Filters(string? Busca, DateOnly? Inicio, DateOnly? Fim, string? Situacao, string? Item, string? Tipo);
}
