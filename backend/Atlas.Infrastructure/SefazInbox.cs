using System.Security.Cryptography;
using Npgsql;

namespace Atlas.Infrastructure;

public sealed record SefazReplayResult(int Status, long Cursor, int Documents, bool AlreadyApplied);

public sealed class SefazInbox(string connectionString)
{
    public async Task<SefazReplayResult> ReplayAsync(
        string cnpj, long sentNsu, bool recovery, byte[] xml, CancellationToken token, bool publish = false)
    {
        if (cnpj.Length != 14 || !cnpj.All(char.IsAsciiDigit))
            throw new ArgumentException("Informe os 14 dígitos do CNPJ.", nameof(cnpj));
        if (sentNsu is < 0 or > 999_999_999_999_999 || (recovery && sentNsu == 0))
            throw new ArgumentOutOfRangeException(nameof(sentNsu));
        if (xml.Length is 0 or > 32 * 1024 * 1024)
            throw new InvalidDataException("O arquivo de resposta está vazio ou excede 32 MiB.");

        var digest = SHA256.HashData(xml);
        await using var db = new NpgsqlConnection(connectionString);
        await db.OpenAsync(token);
        await using (var preserve = new NpgsqlCommand("""
            INSERT INTO sefaz_respostas(cnpj, nsu_enviado, recuperacao, hash, xml)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            """, db))
        {
            preserve.Parameters.AddWithValue(cnpj);
            preserve.Parameters.AddWithValue(sentNsu);
            preserve.Parameters.AddWithValue(recovery);
            preserve.Parameters.AddWithValue(digest);
            preserve.Parameters.AddWithValue(xml);
            await preserve.ExecuteNonQueryAsync(token);
        }

        await using var transaction = await db.BeginTransactionAsync(token);
        await using (var serialize = new NpgsqlCommand(
            "SELECT pg_advisory_xact_lock(718321, hashtext($1))", db, transaction))
        {
            serialize.Parameters.AddWithValue(cnpj);
            await serialize.ExecuteNonQueryAsync(token);
        }

        var (status, savedCursor, savedCount, receivedAt) = await ResponseAsync();
        if (status.HasValue)
        {
            if (publish)
            {
                foreach (var document in SefazDistribution.Parse(xml).Documents)
                    await SefazConsulta.PublishAsync(db, transaction, cnpj, document, token);
                await transaction.CommitAsync(token);
            }
            return new(status.Value, savedCursor, savedCount, true);
        }

        var batch = SefazDistribution.Parse(xml);
        long? current;
        await using (var state = new NpgsqlCommand(
            "SELECT ult_nsu FROM sefaz_estado WHERE cnpj=$1", db, transaction))
        {
            state.Parameters.AddWithValue(cnpj);
            current = (long?)await state.ExecuteScalarAsync(token);
        }

        // A primeira resposta de distribuição usa o cursor registrado no próprio envio.
        // Uma recuperação isolada não fornece o cursor original de distNSU.
        if (current is null && recovery)
            throw new InvalidDataException("Registre uma resposta de distribuição antes de reprocessar uma recuperação.");
        current ??= sentNsu;
        if (!recovery && current != sentNsu)
            throw new InvalidDataException("O NSU enviado não coincide com o cursor da distribuição.");

        long cursor = batch.CursorAfter(current.Value, recovery, sentNsu);
        foreach (var document in batch.Documents)
        {
            await using var insert = new NpgsqlCommand("""
                INSERT INTO sefaz_documentos(cnpj, nsu, schema_xml, xml)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (cnpj, nsu) DO UPDATE SET xml=excluded.xml
                WHERE sefaz_documentos.xml=excluded.xml AND sefaz_documentos.schema_xml=excluded.schema_xml
                """, db, transaction);
            insert.Parameters.AddWithValue(cnpj);
            insert.Parameters.AddWithValue(document.Nsu);
            insert.Parameters.AddWithValue(document.Schema);
            insert.Parameters.AddWithValue(document.Xml);
            if (await insert.ExecuteNonQueryAsync(token) == 0)
                throw new InvalidDataException("O NSU já tem um documento diferente gravado.");
            if (publish)
                await SefazConsulta.PublishAsync(db, transaction, cnpj, document, token);
        }

        DateTime? retry = (recovery && batch.Status != 656 ? null : batch.RetryAt(receivedAt))?.UtcDateTime;
        await using (var update = new NpgsqlCommand("""
            INSERT INTO sefaz_estado(cnpj, ult_nsu, max_nsu, proxima_tentativa)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (cnpj) DO UPDATE SET
                ult_nsu=excluded.ult_nsu,
                max_nsu=coalesce(excluded.max_nsu,sefaz_estado.max_nsu),
                proxima_tentativa=greatest(sefaz_estado.proxima_tentativa,excluded.proxima_tentativa)
            """, db, transaction))
        {
            update.Parameters.AddWithValue(cnpj);
            update.Parameters.AddWithValue(cursor);
            update.Parameters.Add(new() { NpgsqlDbType = NpgsqlTypes.NpgsqlDbType.Bigint,
                Value = !recovery && batch.Status is (137 or 138) && batch.MaxNsu is long max ? max : DBNull.Value });
            update.Parameters.Add(new() { NpgsqlDbType = NpgsqlTypes.NpgsqlDbType.TimestampTz, Value = (object?)retry ?? DBNull.Value });
            await update.ExecuteNonQueryAsync(token);
        }

        await using (var finish = new NpgsqlCommand("""
            UPDATE sefaz_respostas SET cstat=$5, cursor_final=$6, documentos=$7
            WHERE cnpj=$1 AND nsu_enviado=$2 AND recuperacao=$3 AND hash=$4
            """, db, transaction))
        {
            AddKey(finish);
            finish.Parameters.AddWithValue(batch.Status);
            finish.Parameters.AddWithValue(cursor);
            finish.Parameters.AddWithValue(batch.Documents.Count);
            await finish.ExecuteNonQueryAsync(token);
        }
        await transaction.CommitAsync(token);
        return new(batch.Status, cursor, batch.Documents.Count, false);

        async Task<(int? Status, long Cursor, int Count, DateTime ReceivedAt)> ResponseAsync()
        {
            await using var command = new NpgsqlCommand("""
                SELECT cstat, cursor_final, documentos, recebido_em FROM sefaz_respostas
                WHERE cnpj=$1 AND nsu_enviado=$2 AND recuperacao=$3 AND hash=$4
                """, db, transaction);
            AddKey(command);
            await using var rows = await command.ExecuteReaderAsync(token);
            if (!await rows.ReadAsync(token))
                throw new InvalidOperationException("A resposta preservada não foi localizada.");
            return rows.IsDBNull(0) ? (null, 0, 0, rows.GetDateTime(3))
                : (rows.GetInt32(0), rows.GetInt64(1), rows.GetInt32(2), rows.GetDateTime(3));
        }

        void AddKey(NpgsqlCommand command)
        {
            command.Parameters.AddWithValue(cnpj);
            command.Parameters.AddWithValue(sentNsu);
            command.Parameters.AddWithValue(recovery);
            command.Parameters.AddWithValue(digest);
        }
    }
}
