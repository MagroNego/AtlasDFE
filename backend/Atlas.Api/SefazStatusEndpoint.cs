using System.Globalization;
using Atlas.Infrastructure;
using Npgsql;

internal static class SefazStatusEndpoint
{
    public static void MapSefazStatus(this WebApplication app, string connection, string cnpj)
    {
        var store = new SefazStatusStore(connection);
        app.MapGet("/api/sefaz/status", async (IConfiguration configuration, CancellationToken token) =>
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
            timeout.CancelAfter(TimeSpan.FromSeconds(6));
            try
            {
                var snapshot = await store.ReadAsync(cnpj, timeout.Token);
                var now = DateTimeOffset.UtcNow;
                return Results.Ok(new { bancoDisponivel = true, consultadoEm = now, snapshot,
                    agenda = Schedule(configuration, snapshot, now) });
            }
            catch (Exception error) when (error is NpgsqlException or TimeoutException
                || (error is OperationCanceledException && !token.IsCancellationRequested))
            {
                app.Logger.LogWarning("Falha ao consultar Status SEFAZ ({Type}).", error.GetType().Name);
                return Results.Json(new { erro = "Não foi possível consultar o estado SEFAZ no PostgreSQL." },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }
        }).RequireAuthorization();
    }

    private static Agenda Schedule(IConfiguration settings, SefazStatusSnapshot snapshot, DateTimeOffset now)
    {
        bool enabled = false;
        try
        {
            enabled = settings.GetValue<bool>("Sefaz:ScheduleEnabled");
            var (start, end) = SefazSchedule.ReadWindow(settings);
            bool inWindow = SefazDistribution.InWindow(now, start, end);
            bool waiting = snapshot.PausaAte > now;
            SefazSchedule? schedule = enabled ? SefazSchedule.Read(settings) : null;
            DateTimeOffset after = waiting ? snapshot.PausaAte!.Value : now;
            string? problem = enabled && (settings["Sefaz:Uf"] is not { Length: 2 } uf || !uf.All(char.IsAsciiDigit))
                ? "Configure a UF da SEFAZ no serviço." : null;
            if (enabled && string.IsNullOrWhiteSpace(settings["Sefaz:CertificatePath"])
                && (!OperatingSystem.IsWindows() || string.IsNullOrWhiteSpace(settings["Sefaz:CertificateThumbprint"])))
                problem ??= "Configure o certificado da SEFAZ no serviço.";
            // O agendador exige um cursor persistido. Uma resposta local pendente
            // tem prioridade sobre novos envios e depende da próxima execução.
            var next = problem is null && !snapshot.EmExecucao && snapshot.UltNsu.HasValue
                && snapshot.RespostasPendentes == 0 ? schedule?.NextEligible(after, snapshot.UltimaReserva) : null;
            return new(enabled, Format(start), Format(end), inWindow, waiting, next,
                schedule?.Times.Select(Format).ToArray() ?? [], schedule?.Days.Order().Select(day => (int)day).ToArray() ?? [], problem);
        }
        catch (Exception error) when (error is FormatException or InvalidOperationException or ArgumentException)
        {
            return new(enabled, null, null, false, snapshot.PausaAte > now, null, [], [],
                "Confira a configuração da janela e da agenda SEFAZ no serviço.");
        }
    }

    private static string Format(TimeOnly time) => time.ToString("HH:mm", CultureInfo.InvariantCulture);

    private sealed record Agenda(bool Habilitada, string? Inicio, string? Fim, bool NaJanela, bool EmPausa,
        DateTimeOffset? Proxima, string[] Horarios, int[] Dias, string? Diagnostico);
}
