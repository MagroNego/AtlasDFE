using System.Globalization;
using Atlas.Infrastructure;

internal sealed record SefazSchedule(TimeOnly Start, TimeOnly End, TimeOnly[] Times, HashSet<DayOfWeek> Days)
{
    private static readonly TimeSpan Brasilia = TimeSpan.FromHours(-3);
    public static readonly TimeSpan Poll = TimeSpan.FromSeconds(30);

    public static (TimeOnly Start, TimeOnly End) ReadWindow(IConfiguration settings)
    {
        var start = ReadTime(settings["Sefaz:WindowStart"] ?? "22:00");
        var end = ReadTime(settings["Sefaz:WindowEnd"] ?? "04:00");
        if (start == end)
            throw new ArgumentException("O início e o fim da janela devem ser diferentes.");
        return (start, end);
    }

    public static SefazSchedule Read(IConfiguration settings)
    {
        var (start, end) = ReadWindow(settings);
        var times = (settings["Sefaz:ScheduleTimes"] ?? "22:00,23:00,00:00,01:00,02:00,03:00")
            .Split(',', StringSplitOptions.TrimEntries).Select(ReadTime).Distinct().Order().ToArray();
        var intervals = times.Select((time, i) => (i == 0 ? times[^1].ToTimeSpan() - TimeSpan.FromDays(1)
            : times[i - 1].ToTimeSpan(), time.ToTimeSpan()));
        if (intervals.Any(pair => pair.Item2 - pair.Item1 < TimeSpan.FromHours(1))
            || times.Any(time => !SefazDistribution.InWindow(new DateTimeOffset(new DateOnly(2026, 1, 1), time, Brasilia), start, end)))
            throw new InvalidOperationException("Configure horários dentro da janela e separados por pelo menos uma hora.");

        var days = (settings["Sefaz:ScheduleDays"] ?? "0,1,2,3,4,5,6").Split(',', StringSplitOptions.TrimEntries)
            .Select(day => int.TryParse(day, NumberStyles.None, CultureInfo.InvariantCulture, out int value) && value is >= 0 and <= 6
                ? (DayOfWeek)value : throw new InvalidOperationException("Dias válidos vão de 0 (domingo) a 6 (sábado)."))
            .ToHashSet();
        return new(start, end, times, days);
    }

    public DateTimeOffset? CurrentSlot(DateTimeOffset now)
    {
        if (!SefazDistribution.InWindow(now, Start, End))
            return null;
        var local = now.ToOffset(Brasilia);
        var today = DateOnly.FromDateTime(local.DateTime);
        var time = TimeOnly.FromDateTime(local.DateTime);
        var opening = new DateTimeOffset(Start > End && time < End ? today.AddDays(-1) : today, Start, Brasilia);
        return Slots(today.AddDays(-1), 2).Where(slot => slot >= opening && slot <= now)
            .Select(slot => (DateTimeOffset?)slot.ToUniversalTime()).Max();
    }

    // A janela é verificada no instante em que a espera termina, pois um
    // horário ainda não reservado pode ser usado até a janela fechar.
    public DateTimeOffset? NextEligible(DateTimeOffset after, DateTimeOffset? reserved)
    {
        if (CurrentSlot(after) is { } current && (!reserved.HasValue || current > reserved.Value))
            return after.ToUniversalTime();
        var earliest = reserved > after ? reserved.Value : after;
        var day = DateOnly.FromDateTime(earliest.ToOffset(Brasilia).DateTime);
        return Slots(day, 8).Where(slot => slot > earliest)
            .Select(slot => (DateTimeOffset?)slot.ToUniversalTime()).FirstOrDefault();
    }

    private IEnumerable<DateTimeOffset> Slots(DateOnly first, int count) => Enumerable.Range(0, count)
        .Select(first.AddDays)
        .Where(day => Days.Contains(day.DayOfWeek))
        .SelectMany(day => Times.Select(at => new DateTimeOffset(day, at, Brasilia)));

    private static TimeOnly ReadTime(string value) => TimeOnly.ParseExact(value, "HH:mm", CultureInfo.InvariantCulture);
}
