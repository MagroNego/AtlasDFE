using Atlas.Infrastructure;

internal sealed class SefazScheduler(
    IConfiguration settings, string connection, string cnpj, ILogger<SefazScheduler> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (settings["Sefaz:Uf"] is not { Length: 2 } uf || !uf.All(char.IsAsciiDigit))
        {
            logger.LogWarning("Agenda SEFAZ indisponível. Configure Sefaz__Uf.");
            return;
        }
        SefazSchedule schedule;
        try
        {
            schedule = SefazSchedule.Read(settings);
        }
        catch (Exception error) when (error is FormatException or InvalidOperationException or ArgumentException)
        {
            logger.LogWarning("Agenda SEFAZ indisponível. Confira a janela, os dias e os horários configurados.");
            return;
        }

        using var timer = new PeriodicTimer(SefazSchedule.Poll);
        DateTimeOffset? attemptedSlot = null;
        do
        {
            var now = DateTimeOffset.UtcNow;
            var slot = schedule.CurrentSlot(now);
            if (slot is null || slot == attemptedSlot)
                continue;

            try
            {
                using var certificate = SefazCommand.Certificate(settings);
                using var client = SefazSync.Client(certificate);
                var result = await new SefazSync(connection, client).RunAsync(
                    cnpj, uf, null, null, now, schedule.Start, schedule.End, stoppingToken, scheduledSlot: slot);
                if (result is not null)
                {
                    attemptedSlot = slot;
                    logger.LogInformation("Distribuição DF-e cStat {Status}, cursor {Cursor}, documentos {Documents}.",
                        result.Status, result.Cursor, result.Documents);
                }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception error)
            {
                attemptedSlot = slot;
                logger.LogWarning("Falha na sincronização SEFAZ agendada ({Type}).", error.GetType().Name);
            }
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
