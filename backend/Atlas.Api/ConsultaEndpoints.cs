using System.Globalization;
using System.Text;
using System.Text.Json;
using Atlas.Domain;
using Atlas.Infrastructure;
using Microsoft.EntityFrameworkCore;
using ClosedXML.Excel;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http.Features;
using Npgsql;
using NpgsqlTypes;

internal static class ConsultaEndpoints
{
    private const int PageSize = 25;
    private const int MaxResults = 100_000;
    private const int MaxBatchEntries = 10_000;
    private const int MaxTxtBytes = 2 * 1024 * 1024;
    private static readonly string[] BatchHeaders = ["Chave NF-e", "Número", "Série", "Manifestação",
        "Data do evento", "Protocolo", "Código", "Eventos", "Histórico", "Erro", "Situação", "CT-e vinculado"];

    internal static void MapConsulta(this WebApplication app, string connection, string cnpj)
    {
        var group = app.MapGroup("/api/consulta").RequireAuthorization();
        group.MapGet("", async ([AsParameters] Filtros filtros, int? pagina, AtlasDbContext banco, CancellationToken token) =>
        {
            if (pagina is < 1 or > 10000 || !TryResolve(filtros, out var filter))
                return Results.BadRequest(new { erro = "Confira o número, a chave e os filtros informados." });

            var notes = await ReadAsync(connection, cnpj, filter, (pagina.GetValueOrDefault(1) - 1) * PageSize, PageSize + 1, token);
            var importedAt = await banco.Importacoes.Where(row => row.Cnpj == cnpj).Select(row => (DateTime?)row.ImportadoEm).SingleOrDefaultAsync(token);
            return Results.Ok(new { importadoEm = importedAt, notas = notes.Take(PageSize), mais = notes.Count > PageSize });
        });

        group.MapGet("/excel", ([AsParameters] Filtros filtros, CancellationToken token) =>
            ExportAsync(connection, cnpj, filtros, token)).RequireAuthorization(Administration.Export);

        group.MapPost("/excel", async ([AsParameters] Filtros filtros, HttpContext http, IAntiforgery csrf,
            DownloadQueue queue, CancellationToken token) =>
        {
            if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
            if (!TryResolve(filtros, out _))
                return Results.BadRequest(new { erro = "Confira o número, a chave e os filtros informados." });
            return await DownloadEndpoints.EnqueueAsync(http, queue, new("consulta", Consulta: filtros), token);
        }).RequireAuthorization(Administration.Export);

        group.MapPost("/lote", (HttpContext http, IAntiforgery csrf, DownloadQueue queue, CancellationToken token) =>
            Batch(http, csrf, queue, false, token)).RequireAuthorization(Administration.Export);
        group.MapPost("/lote/gerar", (HttpContext http, IAntiforgery csrf, DownloadQueue queue, CancellationToken token) =>
            Batch(http, csrf, queue, true, token)).RequireAuthorization(Administration.Export);

        async Task<IResult> Batch(HttpContext http, IAntiforgery csrf, DownloadQueue queue, bool enqueue, CancellationToken token)
        {
            if (!(enqueue ? await DownloadEndpoints.ValidCsrf(http, csrf) : await csrf.IsRequestValidAsync(http))
                || !http.Request.HasFormContentType)
                return Results.BadRequest(new { erro = "Envie um TXT com uma sessão válida." });
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = MaxTxtBytes + 65536;

            try
            {
                var form = await http.Request.ReadFormAsync(token);
                string mode = form["tipo"].ToString();
                if (mode is not ("chaves" or "numeros") || form.Files.Count != 1 ||
                    !string.Equals(Path.GetExtension(form.Files[0].FileName), ".txt", StringComparison.OrdinalIgnoreCase) ||
                    form.Files[0].Length is <= 0 or > MaxTxtBytes)
                    return Results.BadRequest(new { erro = "Envie um único TXT de até 2 MiB e indique se contém chaves ou números." });

                bool byNumber = mode == "numeros";
                var entries = await ReadEntriesAsync(form.Files[0], byNumber, token);
                return enqueue
                    ? await DownloadEndpoints.EnqueueAsync(http, queue, new("lote", Lote: entries, PorNumero: byNumber), token)
                    : await ExportBatchAsync(connection, cnpj, entries, byNumber, token);
            }
            catch (Exception error) when (error is InvalidDataException or BadHttpRequestException or DecoderFallbackException)
            {
                return Results.BadRequest(new { erro = error is DecoderFallbackException ? "Salve o TXT em UTF-8." : error.Message });
            }
        }
    }

    internal static async Task<IResult> ExportAsync(string connection, string cnpj, Filtros filtros,
        CancellationToken token, Func<string, CancellationToken, Task>? progress = null)
    {
        if (!TryResolve(filtros, out var filter))
            return Results.BadRequest(new { erro = "Confira o número, a chave e os filtros informados." });
        var notes = await ReadAsync(connection, cnpj, filter, 0, MaxResults + 1, token);
        if (progress is not null) await progress("gerando", token);
        var rows = new List<object[]>();
        foreach (var note in notes)
        {
            token.ThrowIfCancellationRequested();
            var events = note.EventosFiltrados;
            string status = Status(note);
            if (events.GetArrayLength() == 0)
                rows.Add([note.Chave, note.Numero, note.Serie, status, "", "", "", "", ""]);
            foreach (var e in events.EnumerateArray())
                rows.Add([note.Chave, note.Numero, note.Serie, status, Value(e, "descricao"),
                    Value(e, "data"), Value(e, "protocolo"), Value(e, "nsu"), Value(e, "correcao")]);
            if (rows.Count > MaxResults)
                return Results.BadRequest(new { erro = "Mais de 100.000 registros. Refine os filtros." });
        }
        return FiscalEndpoints.Excel("Manifestação", FileName(notes, filter.Inicio, filter.Fim),
            ["Chave Acesso", "Número Nota", "Série", "Situação", "Evento", "Data", "Protocolo", "NSU", "Correção"], rows, token);
    }

    internal static async Task<IResult> ExportBatchAsync(string connection, string cnpj, List<IdentificadorNota> entries,
        bool byNumber, CancellationToken token, Func<string, CancellationToken, Task>? progress = null)
    {
        var filter = byNumber
            ? new Resolved(null, entries.Select(entry => entry.Numero!.Value).ToArray(), null, null, null, null)
            : new Resolved(entries.Select(entry => entry.Chave!).ToArray(), null, null, null, null, null);
        var notes = await ReadAsync(connection, cnpj, filter, 0, MaxResults + 1, token, includeEmission: true);
        if (notes.Count > MaxResults)
            return Results.BadRequest(new { erro = "Mais de 100.000 notas correspondem ao TXT. Reduza a lista." });

        if (progress is not null) await progress("gerando", token);
        var byKey = notes.ToDictionary(note => note.Chave, StringComparer.Ordinal);
        var byNoteNumber = notes.ToLookup(note => note.Numero);
        using var book = new XLWorkbook();
        if (byNumber)
        {
            FiscalEndpoints.WriteSheet(book, "Consulta_Manifestacao_YAB", BatchHeaders,
                entries.SelectMany(entry => byNoteNumber[entry.Numero!.Value]).Select(BatchRow), token);
            FiscalEndpoints.WriteSheet(book, "Numeros solicitados", ["Número NF", "Notas localizadas", "Resultado"],
                entries.Select(entry =>
                {
                    int number = entry.Numero!.Value;
                    int count = byNoteNumber[number].Count();
                    return new object[] { number, count, count switch
                    {
                        0 => "Não localizada no banco",
                        1 => "Localizada",
                        _ => "Múltiplas notas; confira chave e série",
                    } };
                }), token);
        }
        else
        {
            FiscalEndpoints.WriteSheet(book, "Consulta_Manifestacao_YAB", BatchHeaders,
                entries.Select(entry => byKey.TryGetValue(entry.Chave!, out var note) ? BatchRow(note)
                    : new object[] { entry.Chave!, int.Parse(entry.Chave!.AsSpan(25, 9), CultureInfo.InvariantCulture),
                        entry.Chave![22..25], "Sem evento localizado", "", "", "", 0, "", "Não localizada no banco", "", "Não localizado" }), token);
        }
        return FiscalEndpoints.DownloadExcel(book, FileName(notes, null, null));
    }

    private static async Task<List<IdentificadorNota>> ReadEntriesAsync(IFormFile file, bool byNumber, CancellationToken token)
    {
        await using var stream = file.OpenReadStream();
        using var reader = new StreamReader(stream, new UTF8Encoding(false, true), detectEncodingFromByteOrderMarks: false);
        var entries = new List<IdentificadorNota>();
        var seen = new HashSet<IdentificadorNota>();
        for (int lineNumber = 1; await reader.ReadLineAsync(token) is { } line; lineNumber++)
        {
            if (lineNumber == 1) line = line.TrimStart('\uFEFF');
            if (string.IsNullOrWhiteSpace(line)) continue;
            var parsed = IdentificadorNota.Parse(line);
            if (parsed is null || byNumber != parsed.Numero.HasValue)
                throw new InvalidDataException($"Linha {lineNumber}: informe {(byNumber ? "um número de NF-e de 1 a 9 dígitos" : "uma chave de NF-e de 44 dígitos")}.");
            if (seen.Add(parsed)) entries.Add(parsed);
            if (entries.Count > MaxBatchEntries)
                throw new InvalidDataException("O TXT excede 10.000 entradas distintas.");
        }
        if (entries.Count == 0) throw new InvalidDataException("O TXT não contém entradas.");
        return entries;
    }

    private static async Task<List<Nota>> ReadAsync(string connection, string cnpj, Resolved filter, int skip, int limit,
        CancellationToken token, bool includeEmission = false)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("""
            WITH notas AS (
                SELECT chave, numero, serie, detalhe FROM consulta_notas
                WHERE cnpj=@cnpj AND (@chaves IS NULL OR chave=ANY(@chaves))
                  AND (@numeros IS NULL OR numero=ANY(@numeros))
                UNION ALL
                SELECT e.chave, e.numero, e.serie, '{"cancelada":false,"eventos":[],"ctes":[]}'::jsonb
                FROM emissao_notas e
                WHERE @inclui_emissao AND e.cnpj=@cnpj AND (@chaves IS NULL OR e.chave=ANY(@chaves))
                  AND (@numeros IS NULL OR e.numero=ANY(@numeros))
                  AND NOT EXISTS (SELECT 1 FROM consulta_notas n WHERE n.cnpj=e.cnpj AND n.chave=e.chave)
            )
            SELECT n.chave, n.numero, n.serie, n.detalhe, coalesce(m.cancelada,false),
                   coalesce(jsonb_array_length(e.itens)>0,false), eventos.filtrados
            FROM notas n
            LEFT JOIN emissao_notas e ON e.cnpj=@cnpj AND e.chave=n.chave
            LEFT JOIN atlas_situacao_atual m ON m.cnpj=@cnpj AND m.chave=n.chave
            CROSS JOIN LATERAL (
                SELECT coalesce((n.detalhe->>'cancelada')::boolean,false) OR coalesce(m.cancelada,false) AS cancelada
            ) estado
            CROSS JOIN LATERAL (
                SELECT CASE WHEN @inicio IS NULL AND @fim IS NULL AND @codigo IS NULL THEN n.detalhe->'eventos'
                    ELSE coalesce((
                        SELECT jsonb_agg(evento ORDER BY posicao)
                        FROM jsonb_array_elements(n.detalhe->'eventos') WITH ORDINALITY AS itens(evento,posicao)
                        CROSS JOIN LATERAL (
                            SELECT CASE
                                WHEN left(evento->>'data',10) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN left(evento->>'data',10)
                                WHEN left(evento->>'data',10) ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
                                  THEN substr(evento->>'data',7,4)||'-'||substr(evento->>'data',4,2)||'-'||left(evento->>'data',2)
                            END AS dia
                        ) datas
                        WHERE (@codigo IS NULL OR evento->>'codigo'=@codigo)
                          AND (@inicio IS NULL OR dia>=@inicio)
                          AND (@fim IS NULL OR dia<=@fim)
                    ), '[]'::jsonb)
                END AS filtrados
            ) eventos
            WHERE (@situacao IS NULL OR estado.cancelada=(@situacao='cancelada'))
              AND ((@inicio IS NULL AND @fim IS NULL AND @codigo IS NULL) OR jsonb_array_length(eventos.filtrados)>0)
            ORDER BY n.chave LIMIT @limite OFFSET @pular
            """, db);
        command.Parameters.AddWithValue("cnpj", cnpj);
        command.Parameters.AddWithValue("inclui_emissao", includeEmission);
        command.Parameters.AddWithValue("limite", limit);
        command.Parameters.AddWithValue("pular", skip);
        command.Parameters.AddWithValue("chaves", NpgsqlDbType.Array | NpgsqlDbType.Text, (object?)filter.Chaves ?? DBNull.Value);
        command.Parameters.AddWithValue("numeros", NpgsqlDbType.Array | NpgsqlDbType.Integer, (object?)filter.Numeros ?? DBNull.Value);
        command.Parameters.AddWithValue("inicio", NpgsqlDbType.Text, (object?)filter.Inicio?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? DBNull.Value);
        command.Parameters.AddWithValue("fim", NpgsqlDbType.Text, (object?)filter.Fim?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? DBNull.Value);
        command.Parameters.AddWithValue("codigo", NpgsqlDbType.Text, (object?)filter.Codigo ?? DBNull.Value);
        command.Parameters.AddWithValue("situacao", NpgsqlDbType.Text, (object?)filter.Situacao ?? DBNull.Value);

        var result = new List<Nota>();
        await using var rows = await command.ExecuteReaderAsync(token);
        while (await rows.ReadAsync(token))
            result.Add(new(rows.GetString(0), rows.GetInt32(1), rows.GetInt32(2),
                FiscalEndpoints.MergeStatus(rows.GetFieldValue<JsonElement>(3), rows.GetBoolean(4)),
                rows.GetBoolean(5), rows.GetFieldValue<JsonElement>(6)));
        return result;
    }

    private static bool TryResolve(Filtros input, out Resolved result)
    {
        result = default!;
        var legacy = IdentificadorNota.Parse(input.Busca);
        var key = IdentificadorNota.Parse(input.Chave)?.Chave;
        var number = IdentificadorNota.Parse(input.Numero)?.Numero;
        if ((!string.IsNullOrWhiteSpace(input.Busca) && (legacy is null || key is not null || number.HasValue))
            || (!string.IsNullOrWhiteSpace(input.Chave) && key is null)
            || (!string.IsNullOrWhiteSpace(input.Numero) && !number.HasValue)
            || input.Inicio > input.Fim
            || input.Codigo is not (null or "" or "110110" or "210200" or "210210" or "210220" or "210240")
            || input.Situacao is not (null or "" or "autorizada" or "cancelada"))
            return false;
        key ??= legacy?.Chave;
        number ??= legacy?.Numero;
        string? code = string.IsNullOrEmpty(input.Codigo) ? null : input.Codigo;
        string? status = string.IsNullOrEmpty(input.Situacao) ? null : input.Situacao;
        result = new(key is null ? null : [key], number.HasValue ? [number.Value] : null, input.Inicio, input.Fim, code, status);
        return key is not null || number.HasValue || input.Inicio.HasValue || input.Fim.HasValue || code is not null || status is not null;
    }

    private static object[] BatchRow(Nota note)
    {
        var events = note.EventosFiltrados.EnumerateArray()
            .OrderBy(e => EventDay(e)).ThenBy(e => Value(e, "data"), StringComparer.Ordinal).ToArray();
        var latest = events.LastOrDefault();
        string history = string.Join('\n', events.Select(e => $"{Value(e, "data")} · {Value(e, "descricao")} · {Value(e, "protocolo")}"));
        return [note.Chave, note.Numero, note.Chave[22..25],
            events.Length == 0 ? "Sem evento localizado" : Value(latest, "descricao"),
            Value(latest, "data"), Value(latest, "protocolo"), Value(latest, "codigo"), events.Length,
            history.Length > 32767 ? history[..32750] + "\n[…]" : history,
            "", Status(note), note.Detalhe.GetProperty("ctes").GetArrayLength() > 0 ? "CT-e Vinculado" : "Não localizado"];
    }

    private static string Status(Nota note) => note.Detalhe.GetProperty("cancelada").GetBoolean() ? "Cancelada" : "Autorizada";

    private static string Value(JsonElement element, string field) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(field, out var value) ? value.GetString() ?? "" : "";

    private static DateOnly? EventDay(JsonElement e)
    {
        var date = Value(e, "data");
        return date.Length >= 10 && DateOnly.TryParseExact(date[..10], ["yyyy-MM-dd", "dd/MM/yyyy"],
            CultureInfo.InvariantCulture, DateTimeStyles.None, out var result) ? result : null;
    }

    private static string FileName(List<Nota> notes, DateOnly? first, DateOnly? last)
    {
        bool explicitStart = first.HasValue, explicitEnd = last.HasValue;
        foreach (var note in notes)
        {
            foreach (var evento in note.EventosFiltrados.EnumerateArray())
            {
                if (EventDay(evento) is not { } day) continue;
                if (!explicitStart && (!first.HasValue || day < first)) first = day;
                if (!explicitEnd && (!last.HasValue || day > last)) last = day;
            }
        }
        string period = first.HasValue || last.HasValue
            ? $"{first?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? "inicio"}-{last?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? "fim"}"
            : "sem_periodo";
        return $"manifestacao_{period}.xlsx";
    }

    internal sealed record Filtros(string? Busca, string? Numero, string? Chave, DateOnly? Inicio, DateOnly? Fim, string? Codigo, string? Situacao);
    private sealed record Resolved(string[]? Chaves, int[]? Numeros, DateOnly? Inicio, DateOnly? Fim, string? Codigo, string? Situacao);
    private sealed record Nota(string Chave, int Numero, int Serie, JsonElement Detalhe, bool TemItens, JsonElement EventosFiltrados);
}
