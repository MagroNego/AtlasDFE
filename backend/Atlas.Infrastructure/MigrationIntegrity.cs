using System.Text.Json;
using Microsoft.Data.Sqlite;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

public sealed record IntegrityMetric(string Nome, long Sqlite, long Postgres);

internal static class MigrationIntegrity
{
    public static void VerifySqlite(SqliteTransaction source, CancellationToken token)
    {
        using var command = source.Connection!.CreateCommand();
        command.Transaction = source;
        command.CommandText = "PRAGMA quick_check(1)";
        token.ThrowIfCancellationRequested();
        if (command.ExecuteScalar() is not string result || result != "ok")
            throw new InvalidDataException("A verificação de integridade do SQLite falhou.");
    }

    public static async Task<List<IntegrityMetric>> MeasureAsync(SqliteTransaction source, NpgsqlTransaction target,
        string cnpj, int notes, long events, long links, string keys, (long Notes, long Items, string Keys)? emission,
        long manual, CancellationToken token)
    {
        await using var command = new NpgsqlCommand("""
            SELECT
              (SELECT count(*) FROM consulta_notas WHERE cnpj=$1),
              (SELECT coalesce(sum(jsonb_array_length(detalhe->'eventos')),0) FROM consulta_notas WHERE cnpj=$1),
              (SELECT coalesce(sum(jsonb_array_length(detalhe->'ctes')),0) FROM consulta_notas WHERE cnpj=$1),
              (SELECT count(*) FROM emissao_notas WHERE cnpj=$1),
              (SELECT coalesce(sum(jsonb_array_length(itens)),0) FROM emissao_notas WHERE cnpj=$1),
              (SELECT count(*) FROM atlas_situacao_manual WHERE cnpj=$1),
              (SELECT ult_nsu FROM sefaz_estado WHERE cnpj=$1),
              (SELECT max_nsu FROM sefaz_estado WHERE cnpj=$1)
            """, target.Connection, target);
        command.Parameters.AddWithValue(cnpj);
        var metrics = new List<IntegrityMetric>();
        await using (var row = await command.ExecuteReaderAsync(token))
        {
            await row.ReadAsync(token);
            metrics.Add(new("Notas na Consulta", notes, row.GetInt64(0)));
            metrics.Add(new("Eventos de NF-e", events, row.GetInt64(1)));
            metrics.Add(new("Vínculos de CT-e", links, row.GetInt64(2)));
            if (emission is { } expected)
            {
                metrics.Add(new("Notas de Emissão", expected.Notes, row.GetInt64(3)));
                metrics.Add(new("Itens de Emissão", expected.Items, row.GetInt64(4)));
            }
            metrics.Add(new("Marcações manuais", manual, row.GetInt64(5)));
            using var sqlite = source.Connection!.CreateCommand();
            sqlite.Transaction = source;
            sqlite.CommandText = "SELECT 1 FROM sqlite_master WHERE type='table' AND name='estado_distribuicao'";
            if (sqlite.ExecuteScalar() is not null)
            {
                sqlite.CommandText = "SELECT ult_nsu,max_nsu FROM estado_distribuicao WHERE cnpj=$cnpj";
                sqlite.Parameters.AddWithValue("$cnpj", cnpj);
                using var cursor = sqlite.ExecuteReader();
                if (cursor.Read())
                {
                    metrics.Add(new("Último NSU", long.Parse(cursor.GetString(0), System.Globalization.CultureInfo.InvariantCulture),
                        row.IsDBNull(6) ? -1 : row.GetInt64(6)));
                    metrics.Add(new("NSU máximo", long.Parse(cursor.GetString(1), System.Globalization.CultureInfo.InvariantCulture),
                        row.IsDBNull(7) ? -1 : row.GetInt64(7)));
                }
            }
        }

        await CheckKeys("Chaves da Consulta", "SELECT chave FROM consulta_notas WHERE cnpj=$1 ORDER BY chave COLLATE \"C\"", keys);
        if (emission is { } stored)
            await CheckKeys("Chaves da Emissão", "SELECT chave FROM emissao_notas WHERE cnpj=$1 ORDER BY chave COLLATE \"C\"", stored.Keys);
        return metrics;

        async Task CheckKeys(string name, string sql, string expected)
        {
            using var hash = CreateHash();
            await using var select = new NpgsqlCommand(sql, target.Connection, target);
            select.Parameters.AddWithValue(cnpj);
            await using var rows = await select.ExecuteReaderAsync(token);
            while (await rows.ReadAsync(token)) AppendKey(hash, rows.GetString(0));
            metrics.Add(new(name, 1, expected == Digest(hash) ? 1 : 0));
        }
    }

    internal static System.Security.Cryptography.IncrementalHash CreateHash() =>
        System.Security.Cryptography.IncrementalHash.CreateHash(System.Security.Cryptography.HashAlgorithmName.SHA256);

    internal static void AppendKey(System.Security.Cryptography.IncrementalHash hash, string key) =>
        hash.AppendData(System.Text.Encoding.UTF8.GetBytes(key + "\n"));

    internal static string Digest(System.Security.Cryptography.IncrementalHash hash) => Convert.ToHexString(hash.GetHashAndReset());

    public static async Task RecordAsync(NpgsqlConnection db, NpgsqlTransaction? tx, string cnpj,
        bool published, List<IntegrityMetric> metrics, CancellationToken token)
    {
        await using var command = new NpgsqlCommand("""
            INSERT INTO atlas_integridade_migracao(cnpj,publicado,medidas) VALUES ($1,$2,$3)
            ON CONFLICT(cnpj) DO UPDATE SET publicado=excluded.publicado,medidas=excluded.medidas,verificado_em=now()
            """, db, tx);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(published);
        command.Parameters.AddWithValue(NpgsqlDbType.Jsonb, JsonSerializer.Serialize(metrics, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
        await command.ExecuteNonQueryAsync(token);
    }
}
