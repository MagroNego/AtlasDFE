using System.Data;
using System.Globalization;
using System.Text.Json;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

public sealed record EmissionReport(string[] Colunas, List<Dictionary<string, object>> Linhas);

public sealed class EmissionReports(string connection)
{
    public const int MaxRows = 100000;
    private static readonly string[] NoteColumns = ["Chave Acesso", "Numero Nota", "Serie", "Data Emissao",
        "Emitente Razao", "Destinatario Razao", "Vl. Nota", "Itens", "Situação", "CT-e vinculado"];
    private static readonly string[] ItemColumns = ["Chave Acesso", "Numero Nota", "Serie", "Data Emissao",
        "Emitente CNPJ", "Emitente Razao", "Destinatario CNPJ", "Destinatario Razao", "Destinatario UF",
        "Item", "Codigo", "Descricao", "NCM", "CFOP", "Unidade", "Qtd", "Vl. Unitario", "Vl. Total",
        "CST ICMS", "Base ICMS", "% ICMS", "Vl. ICMS", "CST IPI", "Base IPI", "% IPI", "Vl. IPI",
        "CST PIS", "Base PIS", "% PIS", "Vl. PIS", "CST COFINS", "Base COFINS", "% COFINS", "Vl. COFINS",
        "CSOSN", "Origem ICMS", "Qtd Base PIS", "Aliquota PIS por unidade", "Qtd Base COFINS", "Aliquota COFINS por unidade",
        "Situação", "CT-e vinculado"];
    private static readonly string[] RetentionColumns = ["Chave Acesso", "Numero Nota", "Serie", "Data Emissao", "Hora Emissao",
        "Emitente CNPJ", "Emitente Razao", "Destinatario CNPJ", "Destinatario Razao", "Vl. PIS Retido", "Vl. COFINS Retido",
        "Situação", "CT-e vinculado"];
    public static readonly string[] SummaryColumns = ["Número de nota", "Item", "Quantidade", "Preço total", "CFOP",
        "Data", "Hora", "Peso líq.", "Peso bruto", "Status"];

    public async Task<EmissionReport> ReadAsync(string cnpj, string kind, string query, DateOnly? start, DateOnly? end,
        string status, string item, string key, int skip, int limit, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        return await ReadAsync(db, null, cnpj, kind, query, start, end, status, item, key, skip, limit, token);
    }

    public async Task<EmissionReport[]> WorkbookAsync(string cnpj, string query, DateOnly? start, DateOnly? end,
        string status, string item, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(IsolationLevel.RepeatableRead, token);
        var reports = new List<EmissionReport>();
        foreach (string kind in new[] { "notas", "itens", "retencoes" })
        {
            var report = await ReadAsync(db, tx, cnpj, kind, query, start, end, status, item, "", 0, MaxRows + 1, token);
            EnsureLimit(report);
            reports.Add(report);
        }
        reports.Add(Summary(reports[1]));
        await tx.CommitAsync(token);
        return reports.ToArray();
    }

    public static void EnsureLimit(EmissionReport report)
    {
        if (report.Linhas.Count > MaxRows)
            throw new InvalidDataException("Relatório excede 100.000 linhas. Refine os filtros.");
    }

    public static EmissionReport Summary(EmissionReport items) => new(SummaryColumns, items.Linhas.Select(row =>
    {
        object Get(string field) => row.GetValueOrDefault(field, "");
        object code = Get("Codigo");
        object[] values = [Get("Numero Nota"), Equals(code, "") ? Get("Item") : code, Get("Qtd"), Get("Vl. Total"),
            Get("CFOP"), Get("Data Emissao"), Get("Hora Emissao"), Get("Peso Liquido"), Get("Peso Bruto"), Get("Situação")];
        return SummaryColumns.Zip(values).ToDictionary(pair => pair.First, pair => pair.Second);
    }).ToList());

    // O legado persiste números formatados em pt-BR. XMLs novos já usam JSON numérico.
    public static object Cell(string field, object value)
    {
        if (value is not string text || text.Length == 0)
            return value;
        if (field.StartsWith("Vl.", StringComparison.Ordinal) || field.StartsWith("Base ", StringComparison.Ordinal)
            || field.StartsWith("% ", StringComparison.Ordinal) || field.StartsWith("Qtd", StringComparison.Ordinal)
            || field.StartsWith("Aliquota ", StringComparison.Ordinal)
            || field is "Quantidade" or "Preço total" or "Peso líq." or "Peso bruto" or "Peso Liquido" or "Peso Bruto")
        {
            string raw = text.Contains(',') ? text.Replace(".", "").Replace(',', '.') : text;
            if (decimal.TryParse(raw, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture, out var number))
                return number;
        }
        return text;
    }

    private static async Task<EmissionReport> ReadAsync(NpgsqlConnection db, NpgsqlTransaction? tx,
        string cnpj, string kind, string query, DateOnly? start, DateOnly? end, string status, string item,
        string key, int skip, int limit, CancellationToken token)
    {
        if (kind is not ("notas" or "itens" or "retencoes"))
            throw new InvalidDataException("Relatório de emissão inválido.");
        string source = kind == "itens"
            ? "CROSS JOIN LATERAL jsonb_array_elements(e.itens) WITH ORDINALITY AS i(dados,ordem)"
            : "CROSS JOIN LATERAL (SELECT coalesce(e.itens->0,'{}'::jsonb) AS dados, 1 AS ordem) i";
        string itemFilter = kind == "itens" ? "strpos(lower(concat_ws(' ',i.dados->>'Codigo',i.dados->>'Descricao',i.dados->>'NCM')),lower($6))>0"
            : "EXISTS (SELECT 1 FROM jsonb_array_elements(e.itens) v WHERE strpos(lower(concat_ws(' ',v->>'Codigo',v->>'Descricao',v->>'NCM')),lower($6))>0)";
        string search = kind switch
        {
            "itens" => "concat_ws(' ',e.chave,e.numero,e.emitente,e.destinatario,e.emissao,(SELECT string_agg(value,' ') FROM jsonb_each_text(i.dados)))",
            "retencoes" => "concat_ws(' ',e.chave,e.numero,e.emitente,e.destinatario,e.emissao,e.pis_retido,e.cofins_retido,i.dados->>'Hora Emissao',i.dados->>'Destinatario CNPJ')",
            _ => "concat_ws(' ',e.chave,e.numero,e.emitente,e.destinatario)",
        };
        await using var command = new NpgsqlCommand($"""
            SELECT e.chave,e.numero,e.serie,e.emissao,e.emitente,e.destinatario,e.valor,jsonb_array_length(e.itens),
                e.pis_retido,e.cofins_retido,i.dados::text,
                coalesce((n.detalhe->>'cancelada')::boolean,false) OR coalesce(m.cancelada,false),
                coalesce(n.detalhe->'ctes','[]'::jsonb)::text
            FROM emissao_notas e LEFT JOIN consulta_notas n USING (cnpj,chave)
            LEFT JOIN atlas_situacao_atual m USING (cnpj,chave) {source}
            WHERE e.cnpj=$1 AND ($2='' OR strpos(lower({search}),lower($2))>0
                OR ($10='notas' AND EXISTS (SELECT 1 FROM jsonb_array_elements(e.itens) v
                    WHERE strpos(lower(concat_ws(' ',v->>'Codigo',v->>'Descricao',v->>'NCM')),lower($2))>0)))
                AND ($3 IS NULL OR e.emissao >= $3) AND ($4 IS NULL OR e.emissao <= $4)
                AND ($5='' OR (coalesce((n.detalhe->>'cancelada')::boolean,false) OR coalesce(m.cancelada,false))=($5='cancelada'))
                AND ($6='' OR {itemFilter}) AND ($7='' OR e.chave=$7)
                AND ($10<>'retencoes' OR e.pis_retido>0 OR e.cofins_retido>0)
            ORDER BY e.emissao DESC,e.chave,i.ordem LIMIT $8 OFFSET $9
            """, db, tx);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(query);
        command.Parameters.Add(new() { NpgsqlDbType = NpgsqlDbType.Date, Value = (object?)start ?? DBNull.Value });
        command.Parameters.Add(new() { NpgsqlDbType = NpgsqlDbType.Date, Value = (object?)end ?? DBNull.Value });
        command.Parameters.AddWithValue(status);
        command.Parameters.AddWithValue(item);
        command.Parameters.AddWithValue(key);
        command.Parameters.AddWithValue(limit);
        command.Parameters.AddWithValue(skip);
        command.Parameters.AddWithValue(kind);
        var result = new EmissionReport(kind == "notas" ? NoteColumns : kind == "itens" ? ItemColumns : RetentionColumns, []);
        await using var reader = await command.ExecuteReaderAsync(token);
        while (await reader.ReadAsync(token))
        {
            using var data = JsonDocument.Parse(reader.GetString(10));
            var row = data.RootElement.EnumerateObject().ToDictionary(field => field.Name, field => field.Value.ValueKind switch
            {
                JsonValueKind.Number when field.Value.TryGetDecimal(out var number) => (object)number,
                JsonValueKind.String => field.Value.GetString() ?? "",
                _ => "",
            });
            row["Chave Acesso"] = reader.GetString(0);
            row["Numero Nota"] = reader.GetInt32(1);
            row["Serie"] = reader.GetInt32(2);
            row["Data Emissao"] = reader.GetFieldValue<DateOnly>(3).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            row["Emitente CNPJ"] = cnpj;
            row["Emitente Razao"] = reader.GetString(4);
            row["Destinatario Razao"] = reader.GetString(5);
            row["Vl. Nota"] = reader.GetDecimal(6);
            row["Itens"] = reader.GetInt32(7);
            row["Vl. PIS Retido"] = reader.GetDecimal(8);
            row["Vl. COFINS Retido"] = reader.GetDecimal(9);
            row["Situação"] = reader.GetBoolean(11) ? "Cancelada" : "Autorizada";
            using var ctes = JsonDocument.Parse(reader.GetString(12));
            row["CT-e vinculado"] = string.Join(" | ", ctes.RootElement.EnumerateArray().Select(cte =>
                cte.TryGetProperty("chave", out var chave) ? chave.GetString() ?? "" : "").Where(chave => chave.Length > 0));
            if (Equals(row["CT-e vinculado"], "")) row["CT-e vinculado"] = "Não localizado";
            foreach (string field in row.Keys.ToArray()) row[field] = Cell(field, row[field]);
            result.Linhas.Add(row);
        }
        return result;
    }
}
