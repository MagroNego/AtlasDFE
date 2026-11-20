using System.Text.Json;
using Atlas.Domain;
using Npgsql;
using NpgsqlTypes;

internal sealed record DownloadRequest(string Tipo, ConsultaEndpoints.Filtros? Consulta = null,
    EmissionReportEndpoints.Filters? Emissao = null, string Chave = "", bool Csv = false,
    List<IdentificadorNota>? Lote = null, bool PorNumero = false);

internal sealed record DownloadEntry(Guid Id, string Tipo, string Estado, DateTimeOffset CriadoEm,
    DateTimeOffset? ConcluidoEm, string? Nome, long? Tamanho, string? Erro, bool Visto);

internal sealed class DownloadQueue(string connection, string cnpj)
{
    internal const int MaxFileBytes = 32 * 1024 * 1024;
    private const string History = "concluido_em IS NULL OR concluido_em > now() - interval '7 days'";

    public async Task<Guid?> EnqueueAsync(string owner, DownloadRequest request, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = Command(db, "SELECT pg_advisory_xact_lock(718325, hashtext($1))"))
        {
            guard.Transaction = tx;
            await guard.ExecuteNonQueryAsync(token);
        }
        var id = Guid.NewGuid();
        await using var insert = Command(db, """
            INSERT INTO atlas_downloads(id,cnpj,usuario,tipo,pedido,estado)
            SELECT $3,$1,$2,$4,$5,'na_fila'
            WHERE (SELECT count(*) FROM atlas_downloads WHERE cnpj=$1 AND concluido_em IS NULL) < 50
              AND (SELECT count(*) FROM atlas_downloads WHERE cnpj=$1 AND usuario=$2 AND concluido_em IS NULL) < 3
              AND (SELECT count(*) FROM atlas_downloads WHERE cnpj=$1 AND usuario=$2
                    AND criado_em > now() - interval '1 minute') < 6
            """, owner, id, request.Tipo);
        insert.Transaction = tx;
        insert.Parameters.AddWithValue(NpgsqlDbType.Jsonb, JsonSerializer.Serialize(request));
        bool inserted = await insert.ExecuteNonQueryAsync(token) != 0;
        await tx.CommitAsync(token);
        return inserted ? id : null;
    }

    public async Task<List<DownloadEntry>> ListAsync(string owner, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var command = Command(db, $"""
            SELECT id,tipo,estado,criado_em,concluido_em,nome,octet_length(arquivo)::bigint,erro,visto
            FROM atlas_downloads WHERE cnpj=$1 AND usuario=$2 AND ({History})
            ORDER BY criado_em DESC,id DESC LIMIT 25
            """, owner);
        await using var rows = await command.ExecuteReaderAsync(token);
        var entries = new List<DownloadEntry>();
        while (await rows.ReadAsync(token))
            entries.Add(new(rows.GetGuid(0), rows.GetString(1), rows.GetString(2), rows.GetFieldValue<DateTimeOffset>(3),
                rows.IsDBNull(4) ? null : rows.GetFieldValue<DateTimeOffset>(4), rows.IsDBNull(5) ? null : rows.GetString(5),
                rows.IsDBNull(6) ? null : rows.GetInt64(6), rows.IsDBNull(7) ? null : rows.GetString(7), rows.GetBoolean(8)));
        return entries;
    }

    public async Task<long> UnreadAsync(string owner, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var command = Command(db, """
            SELECT count(*) FROM (
                SELECT visto,estado FROM atlas_downloads WHERE cnpj=$1 AND usuario=$2
                    AND (concluido_em IS NULL OR concluido_em > now() - interval '7 days')
                ORDER BY criado_em DESC,id DESC LIMIT 25
            ) history WHERE NOT visto AND estado IN ('pronto','falhou')
            """, owner);
        return (long)(await command.ExecuteScalarAsync(token))!;
    }

    public async Task SeenAsync(string owner, Guid[] ids, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var command = Command(db, """
            UPDATE atlas_downloads SET visto=true WHERE cnpj=$1 AND usuario=$2 AND id=ANY($3) AND concluido_em IS NOT NULL
            """, owner, ids);
        await command.ExecuteNonQueryAsync(token);
    }

    public async Task<bool> CancelAsync(string owner, Guid id, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var command = Command(db, """
            UPDATE atlas_downloads SET estado='cancelado', concluido_em=now(), pedido=NULL, tentativa=NULL
            WHERE cnpj=$1 AND usuario=$2 AND id=$3 AND concluido_em IS NULL
            """, owner, id);
        return await command.ExecuteNonQueryAsync(token) != 0;
    }

    public async Task<IResult> FileAsync(string owner, Guid id, CancellationToken token)
    {
        await using var db = await OpenAsync(token);
        await using var command = Command(db, """
            SELECT nome,mime,arquivo FROM atlas_downloads WHERE cnpj=$1 AND usuario=$2 AND id=$3 AND estado='pronto'
                AND concluido_em > now() - interval '7 days'
            """, owner, id);
        await using var rows = await command.ExecuteReaderAsync(token);
        if (!await rows.ReadAsync(token)) return Results.NotFound();
        return Results.File(rows.GetFieldValue<byte[]>(2), rows.GetString(1), rows.GetString(0));
    }

    public async Task<NpgsqlConnection> OpenAsync(CancellationToken token)
    {
        var db = new NpgsqlConnection(connection);
        try { await db.OpenAsync(token); return db; }
        catch { await db.DisposeAsync(); throw; }
    }

    internal NpgsqlCommand Command(NpgsqlConnection db, string sql, params object[] args)
    {
        var command = new NpgsqlCommand(sql, db);
        command.Parameters.AddWithValue(cnpj);
        foreach (var arg in args) command.Parameters.AddWithValue(arg);
        return command;
    }
}
