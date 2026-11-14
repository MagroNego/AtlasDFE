using System.Security.Cryptography.X509Certificates;
using Npgsql;

namespace Atlas.Infrastructure;

public sealed class SefazSync(string connectionString, HttpClient client)
{
    public async Task<SefazReplayResult?> RunAsync(string cnpj, string uf, long? initialCursor,
        long? recoveryNsu, DateTimeOffset now, TimeOnly start, TimeOnly end, CancellationToken token, DateTimeOffset? scheduledSlot = null)
    {
        // Validate identifiers before acquiring locks or preparing a network request.
        SefazDistribution.Request(cnpj, uf, recoveryNsu ?? initialCursor ?? 0, recoveryNsu.HasValue);
        await using var db = new NpgsqlConnection(connectionString);
        await db.OpenAsync(token);
        await using var run = await db.BeginTransactionAsync(token);
        await using (var mutex = new NpgsqlCommand("SELECT pg_try_advisory_xact_lock(718322, hashtext($1))", db, run))
        {
            mutex.Parameters.AddWithValue(cnpj);
            if ((bool?)await mutex.ExecuteScalarAsync(token) != true)
                return null;
        }

        long cursor;
        DateTimeOffset? retry = null;
        await using (var command = new NpgsqlCommand("""
            SELECT ult_nsu, proxima_tentativa FROM sefaz_estado WHERE cnpj=$1
            """, db, run))
        {
            command.Parameters.AddWithValue(cnpj);
            await using var rows = await command.ExecuteReaderAsync(token);
            if (await rows.ReadAsync(token))
            {
                cursor = rows.GetInt64(0);
                if (initialCursor.HasValue && initialCursor.Value != cursor)
                    throw new InvalidDataException("O cursor informado diverge do PostgreSQL.");
                if (!rows.IsDBNull(1))
                    retry = rows.GetFieldValue<DateTimeOffset>(1);
            }
            else
            {
                if (recoveryNsu.HasValue)
                    throw new InvalidDataException("Registre o cursor com uma distribuição antes de recuperar um NSU.");
                cursor = initialCursor ?? throw new InvalidDataException("Informe o cursor atual do Atlas no primeiro uso.");
            }
        }

        var inbox = new SefazInbox(connectionString);
        await using (var pending = new NpgsqlCommand("""
            SELECT nsu_enviado, recuperacao, xml FROM sefaz_respostas
            WHERE cnpj=$1 AND cstat IS NULL ORDER BY recebido_em, nsu_enviado, hash LIMIT 1
            """, db, run))
        {
            pending.Parameters.AddWithValue(cnpj);
            await using var rows = await pending.ExecuteReaderAsync(token);
            if (await rows.ReadAsync(token))
                return await inbox.ReplayAsync(cnpj, rows.GetInt64(0), rows.GetBoolean(1), rows.GetFieldValue<byte[]>(2), token, publish: true);
        }

        if (!SefazDistribution.InWindow(now, start, end) || retry > now)
            return null;
        if (scheduledSlot.HasValue)
        {
            await using var next = new NpgsqlCommand("""
                SELECT nsu FROM sefaz_fila AS q WHERE cnpj=$1 AND status='pendente'
                    AND NOT EXISTS (SELECT 1 FROM sefaz_tentativas t WHERE t.cnpj=q.cnpj AND t.nsu=q.nsu
                        AND t.recuperacao AND t.tentado_em > $2 - interval '1 hour')
                ORDER BY alta DESC, criada_em, nsu LIMIT 1
                """, db, run);
            next.Parameters.AddWithValue(cnpj);
            next.Parameters.AddWithValue(now);
            recoveryNsu = (long?)await next.ExecuteScalarAsync(token);
        }

        long sent = recoveryNsu ?? cursor;
        if (recoveryNsu.HasValue)
        {
            await using var count = new NpgsqlCommand("""
                SELECT CASE WHEN bool_or(nsu=$3) THEN 20 ELSE count(*) END FROM sefaz_tentativas
                WHERE cnpj=$1 AND recuperacao AND tentado_em > $2 - interval '1 hour'
                """, db, run);
            count.Parameters.AddWithValue(cnpj);
            count.Parameters.AddWithValue(now);
            count.Parameters.AddWithValue(sent);
            if ((long?)await count.ExecuteScalarAsync(token) >= 20)
                return null;
        }

        // Commit the attempt independently while the coordination lock stays held.
        await using (var attemptDb = new NpgsqlConnection(connectionString))
        {
            await attemptDb.OpenAsync(token);
            await using var reservation = await attemptDb.BeginTransactionAsync(token);
            await using (var guard = new NpgsqlCommand(
                "SELECT pg_advisory_xact_lock(718320, hashtext($1))", attemptDb, reservation))
            {
                guard.Parameters.AddWithValue(cnpj);
                await guard.ExecuteNonQueryAsync(token);
            }
            if (scheduledSlot is { } slot)
            {
                await using var claim = new NpgsqlCommand("""
                    INSERT INTO sefaz_agenda (cnpj,ultima_reserva) VALUES ($1,$2)
                    ON CONFLICT (cnpj) DO UPDATE SET ultima_reserva=excluded.ultima_reserva
                    WHERE sefaz_agenda.ultima_reserva < excluded.ultima_reserva
                    """, attemptDb, reservation);
                claim.Parameters.AddWithValue(cnpj);
                claim.Parameters.AddWithValue(slot);
                if (await claim.ExecuteNonQueryAsync(token) == 0)
                    return null;
            }

            await using var attempt = new NpgsqlCommand("""
                INSERT INTO sefaz_tentativas(cnpj,recuperacao,tentado_em,nsu) VALUES ($1,$2,$3,$4)
                """, attemptDb, reservation);
            attempt.Parameters.AddWithValue(cnpj);
            attempt.Parameters.AddWithValue(recoveryNsu.HasValue);
            attempt.Parameters.AddWithValue(now);
            attempt.Parameters.AddWithValue(sent);
            await attempt.ExecuteNonQueryAsync(token);
            await reservation.CommitAsync(token);
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(90));
        byte[] response = await SefazDistribution.SendAsync(client,
            SefazDistribution.Request(cnpj, uf, sent, recoveryNsu.HasValue), timeout.Token);
        return await inbox.ReplayAsync(cnpj, sent, recoveryNsu.HasValue, response, token, publish: true);
    }

    public static HttpClient Client(X509Certificate2 certificate)
    {
        if (!certificate.HasPrivateKey || DateTime.UtcNow < certificate.NotBefore.ToUniversalTime()
            || DateTime.UtcNow > certificate.NotAfter.ToUniversalTime())
            throw new InvalidDataException("O certificado precisa estar válido e ter chave privada.");
        var handler = new HttpClientHandler { AllowAutoRedirect = false, ClientCertificateOptions = ClientCertificateOption.Manual };
        handler.ClientCertificates.Add(certificate);
        return new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(90) };
    }
}
