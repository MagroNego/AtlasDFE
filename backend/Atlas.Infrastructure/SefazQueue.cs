using Npgsql;

namespace Atlas.Infrastructure;

public sealed record SefazQueueItem(long Nsu, bool High, string Status);

public sealed class SefazQueue(string connectionString)
{
    public async Task EnqueueAsync(string cnpj, long nsu, bool high, CancellationToken token)
    {
        if (nsu is < 1 or > 999_999_999_999_999)
            throw new ArgumentOutOfRangeException(nameof(nsu));
        await using var db = new NpgsqlConnection(connectionString);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = new NpgsqlCommand("SELECT pg_advisory_xact_lock(718322, hashtext($1))", db, tx))
        {
            guard.Parameters.AddWithValue(cnpj);
            await guard.ExecuteNonQueryAsync(token);
        }
        await using var command = new NpgsqlCommand("""
            INSERT INTO sefaz_fila(cnpj,nsu,alta,status)
            SELECT $1, $2, $3, 'pendente'
            WHERE EXISTS (SELECT 1 FROM sefaz_estado WHERE cnpj=$1 AND max_nsu >= $2)
              AND NOT EXISTS (SELECT 1 FROM sefaz_documentos WHERE cnpj=$1 AND nsu=$2)
              AND (
                EXISTS (SELECT 1 FROM sefaz_fila WHERE cnpj=$1 AND nsu=$2 AND status='pendente')
                OR (SELECT count(*) FROM sefaz_fila WHERE cnpj=$1 AND status='pendente') < 100
              )
            ON CONFLICT (cnpj,nsu) DO UPDATE SET alta=excluded.alta,status='pendente',
                criada_em=CASE WHEN sefaz_fila.status='pendente' THEN sefaz_fila.criada_em ELSE now() END
            """, db, tx);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(nsu);
        command.Parameters.AddWithValue(high);
        if (await command.ExecuteNonQueryAsync(token) == 0)
            throw new InvalidDataException("NSU fora do máximo conhecido, já recebido ou fila cheia.");
        await tx.CommitAsync(token);
    }

    public async Task<bool> CancelAsync(string cnpj, long nsu, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connectionString);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = new NpgsqlCommand("SELECT pg_advisory_xact_lock(718322, hashtext($1))", db, tx))
        {
            guard.Parameters.AddWithValue(cnpj);
            await guard.ExecuteNonQueryAsync(token);
        }
        await using var command = new NpgsqlCommand("""
            UPDATE sefaz_fila SET status='cancelado' WHERE cnpj=$1 AND nsu=$2 AND status='pendente'
            """, db, tx);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(nsu);
        bool changed = await command.ExecuteNonQueryAsync(token) == 1;
        await tx.CommitAsync(token);
        return changed;
    }

    public async Task<IReadOnlyList<SefazQueueItem>> ListAsync(string cnpj, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connectionString);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("""
            SELECT nsu,alta,status FROM sefaz_fila WHERE cnpj=$1
            ORDER BY (status='pendente') DESC, alta DESC, criada_em DESC LIMIT 100
            """, db);
        command.Parameters.AddWithValue(cnpj);
        await using var rows = await command.ExecuteReaderAsync(token);
        var items = new List<SefazQueueItem>();
        while (await rows.ReadAsync(token))
            items.Add(new(rows.GetInt64(0), rows.GetBoolean(1), rows.GetString(2)));
        return items;
    }
}
