using Atlas.Infrastructure;

internal sealed class SefazScheduler(
    OperationalSettings operations, string connection, string cnpj, ILogger<SefazScheduler> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(SefazSchedule.Poll);
        DateTimeOffset? attemptedSlot = null;
        do
        {
            DateTimeOffset? slot = null;
            try
            {
                using var values = await operations.ReadAsync(stoppingToken);
                var settings = values.Configuration;
                if (!settings.GetValue<bool>("Sefaz:ScheduleEnabled"))
                    continue;
                string? uf = settings["Sefaz:Uf"];
                if (!OperationalSettings.ValidUf(uf))
                    continue;

                var schedule = SefazSchedule.Read(settings);
                var now = DateTimeOffset.UtcNow;
                slot = schedule.CurrentSlot(now);
                if (slot is null || slot == attemptedSlot)
                    continue;

                using var certificate = await operations.CertificateAsync(stoppingToken);
                using var client = SefazSync.Client(certificate);
                var result = await new SefazSync(connection, client).RunAsync(
                    cnpj, uf!, null, null, now, schedule.Start, schedule.End, stoppingToken, scheduledSlot: slot);
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
                if (slot.HasValue) attemptedSlot = slot;
                logger.LogWarning("Falha na sincronização SEFAZ agendada ({Type}).", error.GetType().Name);
            }
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
