using System.Globalization;
using Atlas.Infrastructure;

internal sealed class SefazScheduler(
    IConfiguration settings, string connection, string cnpj, ILogger<SefazScheduler> logger) : BackgroundService
{
    private static readonly TimeSpan Brasilia = TimeSpan.FromHours(-3);
    private static readonly TimeSpan Poll = TimeSpan.FromSeconds(30);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        string uf = settings["Sefaz:Uf"] ?? throw new InvalidOperationException("Configure Sefaz__Uf.");
        var start = ReadTime(settings["Sefaz:WindowStart"] ?? "22:00");
        var end = ReadTime(settings["Sefaz:WindowEnd"] ?? "04:00");
        var times = (settings["Sefaz:ScheduleTimes"] ?? "22:00,23:00,00:00,01:00,02:00,03:00")
            .Split(',', StringSplitOptions.TrimEntries).Select(ReadTime).Distinct().Order().ToArray();
        var intervals = times.Select((time, i) => (i == 0 ? times[^1].ToTimeSpan() - TimeSpan.FromDays(1)
            : times[i - 1].ToTimeSpan(), time.ToTimeSpan())).ToArray();
        if (intervals.Any(pair => pair.Item2 - pair.Item1 < TimeSpan.FromHours(1))
            || times.Any(time => !SefazDistribution.InWindow(new DateTimeOffset(new DateOnly(2026, 1, 1), time, Brasilia), start, end)))
            throw new InvalidOperationException("Configure horários dentro da janela e separados por pelo menos uma hora.");
        var days = (settings["Sefaz:ScheduleDays"] ?? "0,1,2,3,4,5,6").Split(',', StringSplitOptions.TrimEntries)
            .Select(day => int.TryParse(day, NumberStyles.None, CultureInfo.InvariantCulture, out int value) && value is >= 0 and <= 6
                ? (DayOfWeek)value : throw new InvalidOperationException("Dias válidos vão de 0 (domingo) a 6 (sábado)."))
            .ToHashSet();

        using var timer = new PeriodicTimer(Poll);
        DateTimeOffset? attemptedSlot = null;
        do
        {
            var now = DateTimeOffset.UtcNow;
            var slot = CurrentSlot(now, start, end, times, days);
            if (slot is null || slot == attemptedSlot)
                continue;

            try
            {
                using var certificate = SefazCommand.Certificate(settings);
                using var client = SefazSync.Client(certificate);
                var result = await new SefazSync(connection, client).RunAsync(
                    cnpj, uf, null, null, now, start, end, stoppingToken, scheduledSlot: slot);
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

    private static TimeOnly ReadTime(string value) =>
        TimeOnly.ParseExact(value, "HH:mm", CultureInfo.InvariantCulture);

    private static DateTimeOffset? CurrentSlot(DateTimeOffset now, TimeOnly start, TimeOnly end,
        TimeOnly[] times, HashSet<DayOfWeek> days)
    {
        if (!SefazDistribution.InWindow(now, start, end))
            return null;
        var local = now.ToOffset(Brasilia);
        var today = DateOnly.FromDateTime(local.DateTime);
        var time = TimeOnly.FromDateTime(local.DateTime);
        var opening = new DateTimeOffset(start > end && time < end ? today.AddDays(-1) : today, start, Brasilia);
        return Enumerable.Range(-1, 2)
            .Select(offset => today.AddDays(offset))
            .Where(day => days.Contains(day.DayOfWeek))
            .SelectMany(day => times.Select(at => new DateTimeOffset(day, at, Brasilia)))
            .Where(slot => slot >= opening && slot <= now)
            .Select(slot => (DateTimeOffset?)slot.ToUniversalTime()).Max();
    }
}
