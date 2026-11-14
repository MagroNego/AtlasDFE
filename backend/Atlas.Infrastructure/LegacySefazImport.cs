using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Data.Sqlite;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

internal sealed class LegacySefazImport(SqliteTransaction source, NpgsqlTransaction target, string cnpj)
{
    public async Task RunAsync(CancellationToken token)
    {
        var tables = new HashSet<string>(StringComparer.Ordinal);
        using (var query = Read("SELECT name FROM sqlite_master WHERE type='table'"))
        using (var rows = query.ExecuteReader())
            while (rows.Read())
                tables.Add(rows.GetString(0));

        foreach (var table in new[] { "sefaz_cobertura", "sefaz_lacunas", "sefaz_fila", "sefaz_respostas", "sefaz_estado" })
        {
            await using var reset = Write($"DELETE FROM {table} WHERE cnpj=$1", cnpj);
            await reset.ExecuteNonQueryAsync(token);
        }

        await using (var attempts = Write("DELETE FROM sefaz_tentativas WHERE cnpj=$1 AND legada", cnpj))
            await attempts.ExecuteNonQueryAsync(token);

        long? cursor = null;
        long? maximum = null;
        DateTime? retry = null;
        if (tables.Contains("estado_distribuicao"))
        {
            using var query = Read("SELECT ult_nsu,max_nsu FROM estado_distribuicao WHERE cnpj=$cnpj");
            using var rows = query.ExecuteReader();
            if (rows.Read())
            {
                cursor = Nsu(rows, 0);
                maximum = Nsu(rows, 1);
                if (maximum < cursor)
                    throw new InvalidDataException("O maxNSU legado é menor que o cursor.");
            }
        }
        foreach (var (table, column) in new[] { ("pausa_distribuicao", "ate_utc"),
            ("controle_sincronizacao", "proxima_tentativa_em") })
        {
            if (!tables.Contains(table))
                continue;
            using var query = Read($"SELECT {column} FROM {table} WHERE cnpj=$cnpj");
            if (query.ExecuteScalar() is string value)
            {
                var limit = Utc(value);
                if (retry is null || limit > retry)
                    retry = limit;
            }
        }
        if (cursor.HasValue)
        {
            await using var state = Write("""
                INSERT INTO sefaz_estado(cnpj,ult_nsu,max_nsu,proxima_tentativa) VALUES ($1,$2,$3,$4)
                """, cnpj, cursor.Value, maximum!.Value);
            state.Parameters.Add(new() { NpgsqlDbType = NpgsqlDbType.TimestampTz, Value = (object?)retry ?? DBNull.Value });
            await state.ExecuteNonQueryAsync(token);
        }

        if (tables.Contains("recuperacoes_nsu"))
            await Transfer("SELECT nsu_local,nsu_sefaz FROM recuperacoes_nsu WHERE cnpj=$cnpj", rows =>
                Write("INSERT INTO sefaz_lacunas VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                    cnpj, Nsu(rows, 0), Nsu(rows, 1)));

        var coverage = new List<string>();
        if (tables.Contains("documentos_nsu"))
            coverage.Add("SELECT nsu FROM documentos_nsu WHERE cnpj=$cnpj");
        if (tables.Contains("consultas_nsu"))
            coverage.Add("SELECT nsu FROM consultas_nsu WHERE cnpj=$cnpj AND resultado IN ('recebido','indisponivel')");
        if (coverage.Count > 0)
        {
            using var query = Read(string.Join(" UNION ", coverage));
            using var rows = query.ExecuteReader();
            await using var copy = await target.Connection!.BeginBinaryImportAsync(
                "COPY sefaz_cobertura (cnpj,nsu) FROM STDIN (FORMAT BINARY)", token);
            var seen = new HashSet<long>();
            while (rows.Read())
            {
                long nsu = Nsu(rows, 0);
                if (!seen.Add(nsu))
                    continue;
                await copy.StartRowAsync(token);
                await copy.WriteAsync(cnpj, NpgsqlDbType.Varchar, token);
                await copy.WriteAsync(nsu, NpgsqlDbType.Bigint, token);
            }
            await copy.CompleteAsync(token);
        }

        if (tables.Contains("fila_nsu_prioridade"))
            await Transfer("""
                SELECT nsu,prioridade,status,criado_em FROM fila_nsu_prioridade
                WHERE cnpj=$cnpj AND status IN ('pendente','consultando','erro','recebido','indisponivel','cancelado')
                """, rows => Write("INSERT INTO sefaz_fila(cnpj,nsu,alta,status,criada_em) VALUES ($1,$2,$3,$4,$5)",
                    cnpj, Nsu(rows, 0), rows.GetInt64(1) != 0,
                    rows.GetString(2) is "consultando" or "erro" ? "pendente" : rows.GetString(2), Utc(rows.GetString(3))));

        if (tables.Contains("consultas_nsu"))
            await Transfer("""
                SELECT nsu,tentado_em FROM consultas_nsu WHERE cnpj=$cnpj
                    AND julianday(tentado_em) >= julianday('now','-1 hour')
                """, rows => Write("""
                INSERT INTO sefaz_tentativas(cnpj,recuperacao,tentado_em,nsu,legada)
                VALUES ($1,true,$2,$3,true)
                """, cnpj, Utc(rows.GetString(1)), Nsu(rows, 0)));
        if (tables.Contains("consultas_pontuais"))
            await Transfer("""
                SELECT consultado_em FROM consultas_pontuais WHERE cnpj=$cnpj
                    AND julianday(consultado_em) >= julianday('now','-1 hour')
                """, rows => Write("""
                INSERT INTO sefaz_tentativas(cnpj,recuperacao,tentado_em,legada)
                VALUES ($1,true,$2,true)
                """, cnpj, Utc(rows.GetString(0))));

        if (tables.Contains("respostas_distribuicao"))
            await Pending("SELECT nsu_enviado,resposta_xml,recebido_em FROM respostas_distribuicao WHERE cnpj=$cnpj AND processado_em IS NULL AND resposta_xml IS NOT NULL", false);
        if (tables.Contains("consultas_nsu"))
            await Pending("SELECT nsu,resposta_xml,tentado_em FROM consultas_nsu WHERE cnpj=$cnpj AND resultado='pendente' AND resposta_xml IS NOT NULL", true);

        async Task Pending(string sql, bool recovery) => await Transfer(sql, rows =>
        {
            byte[] bytes = Encoding.UTF8.GetBytes(rows.GetString(1));
            return Write("""
                INSERT INTO sefaz_respostas(cnpj,nsu_enviado,recuperacao,hash,xml,recebido_em)
                VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
                """, cnpj, Nsu(rows, 0), recovery, SHA256.HashData(bytes), bytes, Utc(rows.GetString(2)));
        });

        async Task Transfer(string sql, Func<SqliteDataReader, NpgsqlCommand> create)
        {
            using var query = Read(sql);
            using var rows = query.ExecuteReader();
            while (rows.Read())
            {
                await using var command = create(rows);
                await command.ExecuteNonQueryAsync(token);
            }
        }
    }

    private SqliteCommand Read(string sql)
    {
        var command = source.Connection!.CreateCommand();
        command.Transaction = source;
        command.CommandText = sql;
        if (sql.Contains("$cnpj", StringComparison.Ordinal))
            command.Parameters.AddWithValue("$cnpj", cnpj);
        return command;
    }

    private NpgsqlCommand Write(string sql, params object[] values)
    {
        var command = new NpgsqlCommand(sql, target.Connection, target);
        foreach (var value in values)
            command.Parameters.AddWithValue(value);
        return command;
    }

    private static long Nsu(SqliteDataReader rows, int ordinal)
    {
        string text = rows.GetString(ordinal);
        if (text.Length is < 1 or > 15 || !text.All(char.IsAsciiDigit))
            throw new InvalidDataException("O banco legado contém um NSU inválido.");
        return long.Parse(text, CultureInfo.InvariantCulture);
    }

    private static DateTime Utc(string value) =>
        DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var date)
            ? date.UtcDateTime : throw new InvalidDataException("O banco legado contém uma data de sincronização inválida.");
}
