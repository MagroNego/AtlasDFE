using System.Text.Json;
using Microsoft.AspNetCore.Http.HttpResults;
using Npgsql;

internal sealed class DownloadWorker(DownloadQueue queue, string connection, string cnpj,
    ILogger<DownloadWorker> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        var cleanupAfter = DateTimeOffset.MinValue;
        do
        {
            try
            {
                await using var db = await queue.OpenAsync(stoppingToken);
                await using var guard = await db.BeginTransactionAsync(stoppingToken);
                await using var take = queue.Command(db, "SELECT pg_try_advisory_xact_lock(718324, hashtext($1))");
                take.Transaction = guard;
                if ((bool?)await take.ExecuteScalarAsync(stoppingToken) != true) continue;

                // Enquanto o lock está ativo, só esta instância gera relatórios deste CNPJ.
                // Uma tentativa interrompida reaparece na fila após a próxima aquisição.
                await using var work = await queue.OpenAsync(stoppingToken);
                await Execute(work, """
                    UPDATE atlas_downloads SET estado='na_fila',tentativa=NULL
                    WHERE cnpj=$1 AND concluido_em IS NULL AND tentativa IS NOT NULL
                    """, stoppingToken);
                if (DateTimeOffset.UtcNow >= cleanupAfter)
                {
                    await Execute(work, """
                        DELETE FROM atlas_downloads WHERE id IN (
                            SELECT id FROM (
                                SELECT id,concluido_em,row_number() OVER(PARTITION BY usuario ORDER BY criado_em DESC,id DESC) AS ordem
                                FROM atlas_downloads WHERE cnpj=$1 AND concluido_em IS NOT NULL
                            ) history WHERE ordem>20 OR concluido_em<=now()-interval '7 days'
                        )
                        """, stoppingToken);
                    cleanupAfter = DateTimeOffset.UtcNow.AddMinutes(1);
                }
                var job = await ClaimAsync(work, stoppingToken);
                if (job is not null) await GenerateAsync(work, job, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { return; }
            catch (Exception error)
            {
                log.LogWarning("Falha na fila de downloads ({Type}).", error.GetType().Name);
            }
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task<Job?> ClaimAsync(NpgsqlConnection db, CancellationToken token)
    {
        var attempt = Guid.NewGuid();
        await using var command = queue.Command(db, """
            UPDATE atlas_downloads SET estado='consultando',tentativa=$2 WHERE id=(
                SELECT id FROM atlas_downloads WHERE cnpj=$1 AND estado='na_fila'
                ORDER BY criado_em,id LIMIT 1 FOR UPDATE SKIP LOCKED
            ) AND estado='na_fila' RETURNING id,pedido::text
            """, attempt);
        await using var rows = await command.ExecuteReaderAsync(token);
        if (!await rows.ReadAsync(token)) return null;
        return new(rows.GetGuid(0), attempt, rows.GetString(1));
    }

    private async Task GenerateAsync(NpgsqlConnection db, Job job, CancellationToken shutdown)
    {
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(shutdown);
        using var stopMonitor = new CancellationTokenSource();
        cancellation.CancelAfter(TimeSpan.FromMinutes(5));
        var monitor = MonitorAsync(job, cancellation, stopMonitor.Token);

        async Task Progress(string state, CancellationToken token)
        {
            int changed = await Execute(db, """
                UPDATE atlas_downloads SET estado=$4 WHERE cnpj=$1 AND id=$2 AND tentativa=$3 AND concluido_em IS NULL
                """, token, job.Id, job.Attempt, state);
            if (changed == 0) cancellation.Cancel();
            token.ThrowIfCancellationRequested();
        }

        try
        {
            var token = cancellation.Token;
            var request = JsonSerializer.Deserialize<DownloadRequest>(job.Pedido)
                ?? throw new InvalidDataException("Pedido de download inválido.");
            IResult result = request.Tipo switch
            {
                "consulta" => await ConsultaEndpoints.ExportAsync(connection, cnpj, request.Consulta!, token, Progress),
                "lote" => await ConsultaEndpoints.ExportBatchAsync(connection, cnpj, request.Lote!, request.PorNumero, token, Progress),
                "importacao" => await XmlImportHistory.ExportAsync(connection, cnpj, request.Importacao, token, Progress),
                _ => await EmissionReportEndpoints.ExportAsync(connection, cnpj, request.Emissao!,
                    request.Chave, request.Csv, token, Progress),
            };
            token.ThrowIfCancellationRequested();
            if (result is not FileContentHttpResult file)
            {
                string message = "Não foi possível gerar o relatório.";
                if (result is IValueHttpResult { Value: not null } value
                    && JsonSerializer.SerializeToElement(value.Value).TryGetProperty("erro", out var error))
                    message = error.GetString() ?? message;
                await FailAsync(db, job, message, shutdown);
                return;
            }
            if (file.FileContents.Length > DownloadQueue.MaxFileBytes)
            {
                await FailAsync(db, job, "O arquivo excede 32 MiB. Refine os filtros.", shutdown);
                return;
            }
            await Progress("salvando", token);
            await Execute(db, """
                UPDATE atlas_downloads SET estado='pronto',concluido_em=now(),pedido=NULL,tentativa=NULL,
                    nome=$4,mime=$5,arquivo=$6
                WHERE cnpj=$1 AND id=$2 AND tentativa=$3 AND concluido_em IS NULL
                """, token, job.Id, job.Attempt, file.FileDownloadName!, file.ContentType, file.FileContents.ToArray());
        }
        catch (OperationCanceledException) when (shutdown.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (error is not OperationCanceledException)
                log.LogWarning("Falha na geração de relatório ({Type}).", error.GetType().Name);
            await FailAsync(db, job, error is OperationCanceledException
                ? "A geração foi interrompida ou excedeu o tempo limite. Solicite o arquivo novamente."
                : "Não foi possível gerar o relatório.", shutdown);
        }
        finally
        {
            stopMonitor.Cancel();
            await monitor;
        }
    }

    private async Task MonitorAsync(Job job, CancellationTokenSource cancellation, CancellationToken stop)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        try
        {
            while (await timer.WaitForNextTickAsync(stop))
            {
                await using var db = await queue.OpenAsync(stop);
                await using var command = queue.Command(db, """
                    SELECT EXISTS(SELECT 1 FROM atlas_downloads WHERE cnpj=$1 AND id=$2 AND tentativa=$3 AND concluido_em IS NULL)
                    """, job.Id, job.Attempt);
                if ((bool?)await command.ExecuteScalarAsync(stop) != true)
                {
                    cancellation.Cancel();
                    return;
                }
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
        catch (NpgsqlException) { cancellation.Cancel(); }
    }

    private Task<int> FailAsync(NpgsqlConnection db, Job job, string message, CancellationToken token) =>
        Execute(db, """
            UPDATE atlas_downloads SET estado='falhou',concluido_em=now(),pedido=NULL,tentativa=NULL,erro=$4
            WHERE cnpj=$1 AND id=$2 AND tentativa=$3 AND concluido_em IS NULL
            """, token, job.Id, job.Attempt, message);

    private async Task<int> Execute(NpgsqlConnection db, string sql, CancellationToken token, params object[] args)
    {
        await using var command = queue.Command(db, sql, args);
        return await command.ExecuteNonQueryAsync(token);
    }

    private sealed record Job(Guid Id, Guid Attempt, string Pedido);
}
