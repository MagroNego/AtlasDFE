using System.Data;
using Npgsql;

namespace Atlas.Infrastructure;

public sealed record SefazStatusEntry(DateTimeOffset Em, long? Nsu, bool Recuperacao, string Tipo,
    int? Cstat, long? Cursor, int? Documentos);
public sealed record SefazStatusSnapshot(long? UltNsu, long? MaxNsu, DateTimeOffset? PausaAte,
    DateTimeOffset? UltimaGravacao, DateTimeOffset? UltimaReserva, long RespostasPendentes,
    DateTimeOffset? PendenteDesde, long FilaPendente, bool EmExecucao, List<SefazStatusEntry> Atividade);

public sealed class SefazStatusStore(string connection)
{
    public async Task<SefazStatusSnapshot> ReadAsync(string cnpj, CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(IsolationLevel.RepeatableRead, token);
        var status = new NpgsqlBatchCommand("""
            SELECT e.ult_nsu, e.max_nsu, e.proxima_tentativa,
                (SELECT max(processado_em) FROM sefaz_respostas WHERE cnpj=$1 AND processado_em IS NOT NULL),
                (SELECT ultima_reserva FROM sefaz_agenda WHERE cnpj=$1),
                p.total, p.desde,
                (SELECT count(*) FROM sefaz_fila WHERE cnpj=$1 AND status='pendente'),
                EXISTS (SELECT 1 FROM pg_locks
                    WHERE locktype='advisory' AND granted AND classid=718322 AND objsubid=2
                      AND objid=(hashtext($1)::bigint & 4294967295)::oid
                      AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))
            FROM (SELECT count(*) AS total, min(recebido_em) AS desde
                FROM sefaz_respostas WHERE cnpj=$1 AND cstat IS NULL) p
            LEFT JOIN sefaz_estado e ON e.cnpj=$1
            """);
        var activity = new NpgsqlBatchCommand("""
            SELECT em, nsu, recuperacao, tipo, cstat, cursor_final, documentos FROM (
                (SELECT recebido_em AS em, nsu_enviado AS nsu, recuperacao, 'resposta' AS tipo,
                    cstat, cursor_final, documentos
                 FROM sefaz_respostas WHERE cnpj=$1 ORDER BY recebido_em DESC, nsu_enviado DESC, recuperacao, hash LIMIT 25)
                UNION ALL
                (SELECT tentado_em, nsu, recuperacao, 'envio', NULL, NULL, NULL
                 FROM sefaz_tentativas WHERE cnpj=$1 AND NOT legada ORDER BY tentado_em DESC, nsu DESC, recuperacao LIMIT 25)
            ) entries ORDER BY em DESC, tipo, nsu DESC LIMIT 25
            """);
        status.Parameters.AddWithValue(cnpj);
        activity.Parameters.AddWithValue(cnpj);
        await using var batch = new NpgsqlBatch(db, tx) { Timeout = 5 };
        batch.BatchCommands.Add(status);
        batch.BatchCommands.Add(activity);
        await using var rows = await batch.ExecuteReaderAsync(token);
        await rows.ReadAsync(token);
        var entries = new List<SefazStatusEntry>();
        var snapshot = new SefazStatusSnapshot(Value<long>(0), Value<long>(1), Value<DateTimeOffset>(2),
            Value<DateTimeOffset>(3), Value<DateTimeOffset>(4), rows.GetInt64(5), Value<DateTimeOffset>(6),
            rows.GetInt64(7), rows.GetBoolean(8), entries);
        await rows.NextResultAsync(token);
        while (await rows.ReadAsync(token))
            entries.Add(new(rows.GetFieldValue<DateTimeOffset>(0), Value<long>(1), rows.GetBoolean(2),
                rows.GetString(3), Value<int>(4), Value<long>(5), Value<int>(6)));
        return snapshot;

        T? Value<T>(int index) where T : struct => rows.IsDBNull(index) ? null : rows.GetFieldValue<T>(index);
    }
}
