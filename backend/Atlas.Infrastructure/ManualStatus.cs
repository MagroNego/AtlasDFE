using Atlas.Domain;
using Microsoft.Data.Sqlite;
using System.Globalization;
using Npgsql;

namespace Atlas.Infrastructure;

public sealed record StatusChange(string Chave, bool Cancelada, string Usuario, string Motivo, DateTime AlteradoEm);

public sealed class ManualStatus(string connection)
{
    public async Task ApplyAsync(string cnpj, string identifier, bool canceled, string user, string reason, CancellationToken token)
    {
        var note = IdentificadorNota.Parse(identifier);
        reason = reason.Trim();
        if (note is null || reason.Length is < 1 or > 500)
            throw new InvalidDataException("Informe a nota e um motivo de até 500 caracteres.");
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = new NpgsqlCommand("SELECT pg_advisory_xact_lock(718320, hashtext($1))", db, tx))
        {
            guard.Parameters.AddWithValue(cnpj);
            await guard.ExecuteNonQueryAsync(token);
        }
        await using var lookup = new NpgsqlCommand("""
            SELECT chave FROM (
                SELECT chave,numero FROM consulta_notas WHERE cnpj=$1
                UNION SELECT chave,numero FROM emissao_notas WHERE cnpj=$1
            ) AS notes WHERE ($2::text IS NOT NULL AND chave=$2) OR numero=$3::integer LIMIT 2
            """, db, tx);
        lookup.Parameters.AddWithValue(cnpj);
        lookup.Parameters.AddWithValue(NpgsqlTypes.NpgsqlDbType.Text, (object?)note.Chave ?? DBNull.Value);
        lookup.Parameters.AddWithValue(NpgsqlTypes.NpgsqlDbType.Integer, (object?)note.Numero ?? DBNull.Value);
        string key;
        await using (var rows = await lookup.ExecuteReaderAsync(token))
        {
            if (!await rows.ReadAsync(token))
                throw new InvalidDataException("NF-e não localizada.");
            key = rows.GetString(0);
            if (await rows.ReadAsync(token))
                throw new InvalidDataException("Número ambíguo. Informe a chave de acesso.");
        }
        await using var save = new NpgsqlCommand("""
            INSERT INTO atlas_situacao_manual(cnpj,chave,usuario,motivo,cancelada) VALUES ($1,$2,$3,$4,$5)
            """, db, tx);
        foreach (var value in new object[] { cnpj, key, user, reason, canceled })
            save.Parameters.AddWithValue(value);
        await save.ExecuteNonQueryAsync(token);
        await tx.CommitAsync(token);
    }

    internal static async Task ImportAsync(SqliteTransaction source, NpgsqlTransaction destination, string cnpj, CancellationToken token)
    {
        using var read = source.Connection!.CreateCommand();
        read.Transaction = source;
        read.CommandText = "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('historico_cancelamentos_manuais','cancelamentos_manuais')";
        var tables = new HashSet<string>();
        using (var names = read.ExecuteReader())
            while (names.Read()) tables.Add(names.GetString(0));
        await using var clear = new NpgsqlCommand("DELETE FROM atlas_situacao_manual WHERE cnpj=$1", destination.Connection, destination);
        clear.Parameters.AddWithValue(cnpj);
        await clear.ExecuteNonQueryAsync(token);
        read.Parameters.AddWithValue("$cnpj", cnpj);
        if (tables.Contains("historico_cancelamentos_manuais"))
            await Copy("""
                SELECT chave,usuario,motivo,acao='cancelar',alterado_em
                FROM historico_cancelamentos_manuais WHERE cnpj=$cnpj ORDER BY id
                """);
        if (tables.Contains("cancelamentos_manuais"))
        {
            string filter = tables.Contains("historico_cancelamentos_manuais")
                ? "AND NOT EXISTS (SELECT 1 FROM historico_cancelamentos_manuais h WHERE h.cnpj=m.cnpj AND h.chave=m.chave)"
                : "";
            await Copy($"""
                SELECT chave,usuario,motivo,1,alterado_em
                FROM cancelamentos_manuais m WHERE cnpj=$cnpj {filter}
                """);
        }

        async Task Copy(string sql)
        {
            read.CommandText = sql;
            using var rows = read.ExecuteReader();
            while (rows.Read())
            {
                token.ThrowIfCancellationRequested();
                if (!DateTimeOffset.TryParse(rows.GetString(4), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var time))
                    throw new InvalidDataException("Data inválida no histórico de cancelamento manual.");
                await using var insert = new NpgsqlCommand("""
                    INSERT INTO atlas_situacao_manual(cnpj,chave,usuario,motivo,cancelada,alterado_em)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    """, destination.Connection, destination);
                foreach (var value in new object[] { cnpj, rows.GetString(0), rows.GetString(1), rows.GetString(2),
                    rows.GetBoolean(3), time.UtcDateTime })
                    insert.Parameters.AddWithValue(value);
                await insert.ExecuteNonQueryAsync(token);
            }
        }
    }

    public async Task<List<StatusChange>> HistoryAsync(string cnpj, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("""
            SELECT chave,cancelada,usuario,motivo,alterado_em
            FROM atlas_situacao_manual WHERE cnpj=$1 ORDER BY id DESC LIMIT 100
            """, db);
        command.Parameters.AddWithValue(cnpj);
        await using var rows = await command.ExecuteReaderAsync(token);
        var changes = new List<StatusChange>();
        while (await rows.ReadAsync(token))
            changes.Add(new(rows.GetString(0), rows.GetBoolean(1), rows.GetString(2), rows.GetString(3), rows.GetDateTime(4)));
        return changes;
    }
}
