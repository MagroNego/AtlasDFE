using System.Net.Mail;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.EntityFrameworkCore;

internal static class OperationalEndpoints
{
    private const int PfxLimit = 1024 * 1024;

    public static void MapOperationalControls(this WebApplication app, string connection, string cnpj)
    {
        var group = app.MapGroup("/api/admin/sefaz").RequireAuthorization(policy => policy.RequireRole(Administration.Role));
        group.AddEndpointFilter(async (context, next) =>
        {
            var http = context.HttpContext;
            if (!HttpMethods.IsGet(http.Request.Method)
                && (!http.Request.Headers.ContainsKey("X-CSRF-TOKEN")
                    || !await http.RequestServices.GetRequiredService<IAntiforgery>().IsRequestValidAsync(http)))
                return Error("Sessão expirada. Atualize a página.");
            return await next(context);
        });

        group.MapGet("", async (OperationalSettings operations, CancellationToken token) =>
        {
            using var values = await operations.ReadAsync(token);
            var settings = values.Configuration;
            var days = (settings["Sefaz:ScheduleDays"] ?? "0,1,2,3,4,5,6").Split(',')
                .Select(day => int.TryParse(day, out var number) ? number : -1).Where(day => day is >= 0 and <= 6);
            return Results.Ok(new
            {
                agenda = new OperationalSettings.AgendaInput(bool.TryParse(settings["Sefaz:ScheduleEnabled"], out bool enabled) && enabled,
                    settings["Sefaz:WindowStart"] ?? "22:00", settings["Sefaz:WindowEnd"] ?? "04:00",
                    settings["Sefaz:ScheduleTimes"] ?? "22:00,23:00,00:00,01:00,02:00,03:00", days.ToArray(), settings["Sefaz:Uf"]),
                alertas = new OperationalSettings.AlertsInput(bool.TryParse(settings["Smtp:Enabled"], out bool alerts) && alerts,
                    settings["Smtp:Recipients"] ?? ""),
                servidorSmtp = !string.IsNullOrWhiteSpace(settings["Smtp:Host"]) && !string.IsNullOrWhiteSpace(settings["Smtp:From"]),
                certificado = values.Certificate,
                certificadoDisponivel = values.HasCertificate,
            });
        });

        group.MapPut("/agenda", async (OperationalSettings.AgendaInput input, OperationalSettings operations,
            AtlasDbContext db, HttpContext http, CancellationToken token) =>
        {
            if (input.Inicio is not { Length: 5 } || input.Fim is not { Length: 5 }
                || input.Horarios is not { Length: > 0 and <= 200 }
                || input.Dias is not { Length: > 0 and <= 7 } || input.Dias.Any(day => day is < 0 or > 6))
                return Error("Informe a janela, os horários separados por vírgulas e os dias da semana.");
            var data = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Sefaz:WindowStart"] = input.Inicio, ["Sefaz:WindowEnd"] = input.Fim,
                ["Sefaz:ScheduleTimes"] = input.Horarios,
                ["Sefaz:ScheduleDays"] = string.Join(',', input.Dias),
            }).Build();
            using var configuration = (IDisposable)data;
            try { SefazSchedule.Read(data); }
            catch (Exception error) when (error is FormatException or ArgumentException or InvalidOperationException)
            {
                return Error("Os horários devem estar dentro da janela e ter pelo menos uma hora de intervalo.");
            }
            using var current = await operations.ReadAsync(token);
            string? uf = string.IsNullOrWhiteSpace(input.Uf) ? current.Configuration["Sefaz:Uf"] : input.Uf;
            if ((uf is { Length: > 0 } || input.Habilitada) && !OperationalSettings.ValidUf(uf))
                return Error("Selecione uma UF válida para a SEFAZ.");
            await operations.SaveAsync(db, Actor(http), "agenda", input with { Uf = uf }, null, token);
            return Results.NoContent();
        });

        group.MapPut("/alertas", async (OperationalSettings.AlertsInput input, OperationalSettings operations,
            AtlasDbContext db, HttpContext http, CancellationToken token) =>
        {
            var recipients = Recipients(input.Destinatarios);
            if (recipients is null || (input.Habilitados && recipients.Length == 0))
                return Error("Informe até 30 endereços de e-mail válidos, separados por vírgula ou linha.");
            await operations.SaveAsync(db, Actor(http), "alertas",
                input with { Destinatarios = string.Join(',', recipients) }, null, token);
            return Results.NoContent();
        });

        group.MapPost("/alertas/teste", async (MailInput input, OperationalSettings operations,
            AtlasDbContext db, HttpContext http, CancellationToken token) =>
        {
            using var values = await operations.ReadAsync(token);
            string? email = Recipients(input.Email) is [var single] ? single : null;
            if (email is null || !(Recipients(values.Configuration["Smtp:Recipients"]) ?? []).Contains(email))
                return Error("Selecione um destinatário da lista salva.");
            await Administration.AuditAsync(db, cnpj, Actor(http), "operacao.alertas.teste", email, token);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
            timeout.CancelAfter(TimeSpan.FromSeconds(20));
            try { await SmtpAlerts.SendAsync(values.Configuration, null, email, timeout.Token); }
            catch (Exception error) when (!token.IsCancellationRequested)
            {
                app.Logger.LogWarning("Falha no teste SMTP ({Type}).", error.GetType().Name);
                return Error("Falha no teste. Confira o servidor SMTP e os logs do serviço.", 502);
            }
            return Results.NoContent();
        }).RequireRateLimiting("teste-smtp");

        group.MapPost("/alertas/reagendar", async (OperationalSettings operations, AtlasDbContext db,
            HttpContext http, CancellationToken token) =>
        {
            using var values = await operations.ReadAsync(token);
            var recipients = Recipients(values.Configuration["Smtp:Recipients"]);
            if (!SmtpAlerts.Enabled(values.Configuration) || recipients is not { Length: > 0 })
                return Error("Ative os alertas e salve a lista de destinatários.");
            await using var tx = await db.Database.BeginTransactionAsync(token);
            var locked = await db.Database.SqlQuery<bool>($"""
                SELECT pg_try_advisory_xact_lock(718323, hashtext({cnpj})) AS "Value"
                """).SingleAsync(token);
            if (!locked)
                return Error("O serviço está enviando um alerta. Tente novamente.", 409);
            int count = await db.Database.ExecuteSqlInterpolatedAsync($"""
                UPDATE atlas_alertas_entregas SET tentar_em=now()
                WHERE cnpj={cnpj} AND email=ANY({recipients}) AND enviado_em IS NULL AND tentar_em>now()
                """, token);
            await Administration.AuditAsync(db, cnpj, Actor(http), "operacao.alertas.reagendar", "", token);
            await tx.CommitAsync(token);
            return Results.Ok(new { reagendados = count });
        }).RequireRateLimiting("teste-smtp");

        group.MapPost("/certificado", async (HttpContext http, OperationalSettings operations, AtlasDbContext db,
            CancellationToken token) =>
        {
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = PfxLimit + 16 * 1024;
            if (!http.Request.HasFormContentType)
                return Error("Envie um arquivo PFX ou P12 e a senha do certificado.");
            byte[]? raw = null;
            try
            {
                var formOptions = new FormOptions
                {
                    MemoryBufferThreshold = PfxLimit + 16 * 1024,
                    MultipartBodyLengthLimit = PfxLimit,
                    ValueLengthLimit = 256,
                    ValueCountLimit = 2,
                };
                var form = await http.Request.ReadFormAsync(formOptions, token);
                if (form.Files.Count != 1 || form.Files.GetFile("arquivo") is not { Length: > 0 and <= PfxLimit } file
                    || form["senha"].Count > 1 || form["senha"].ToString().Length > 256)
                    return Error("Envie um certificado de até 1 MiB e uma senha de até 256 caracteres.");
                raw = new byte[(int)file.Length];
                await using var source = file.OpenReadStream();
                await source.ReadExactlyAsync(raw, token);
                string password = form["senha"].ToString();
                using var certificate = X509CertificateLoader.LoadPkcs12(raw, password, X509KeyStorageFlags.EphemeralKeySet);
                using var client = SefazSync.Client(certificate);
                var info = new OperationalSettings.CertificateInfo(certificate.Thumbprint,
                    certificate.NotBefore.ToUniversalTime(), certificate.NotAfter.ToUniversalTime());
                await operations.SaveAsync(db, Actor(http), "certificado", info, operations.Protect(raw, password), token);
                return Results.NoContent();
            }
            catch (Exception error) when (error is CryptographicException or InvalidDataException or BadHttpRequestException)
            {
                return Error("Não foi possível abrir o certificado. Confira o arquivo, a senha, a chave privada e a validade.");
            }
            finally
            {
                if (raw is not null) CryptographicOperations.ZeroMemory(raw);
            }
        });

        group.MapDelete("/certificado", async (OperationalSettings operations, AtlasDbContext db,
            HttpContext http, CancellationToken token) =>
        {
            await operations.RemoveCertificateAsync(db, Actor(http), token);
            return Results.NoContent();
        });

        group.MapPost("/sincronizar", async (OperationalSettings operations, AtlasDbContext db,
            HttpContext http, CancellationToken token) =>
        {
            try
            {
                using var values = await operations.ReadAsync(token);
                string? uf = values.Configuration["Sefaz:Uf"];
                if (!OperationalSettings.ValidUf(uf))
                    return Error("Selecione a UF em Controles SEFAZ e salve a agenda.");
                var (start, end) = SefazSchedule.ReadWindow(values.Configuration);
                var snapshot = await new SefazStatusStore(connection).ReadAsync(cnpj, token);
                if (snapshot.EmExecucao) return Error("Já existe uma sincronização em andamento.", 409);
                if (snapshot.UltNsu is null) return Error("Importe ou inicialize o cursor antes da sincronização pela web.", 409);
                var now = DateTimeOffset.UtcNow;
                if (snapshot.RespostasPendentes == 0 && (!SefazDistribution.InWindow(now, start, end) || snapshot.PausaAte > now))
                    return Error("A coleta está fora da janela ou em pausa. Confira o Status SEFAZ.", 409);
                await Administration.AuditAsync(db, cnpj, Actor(http), "operacao.sincronizar", "", token);
                using var certificate = await operations.CertificateAsync(token);
                using var client = SefazSync.Client(certificate);
                var result = await new SefazSync(connection, client).RunAsync(cnpj, uf!, null, null,
                    DateTimeOffset.UtcNow, start, end, token, manual: true);
                return result is null
                    ? Error("A consulta não foi enviada. Confira a janela, a pausa e a última tentativa no Status SEFAZ.", 409)
                    : Results.Ok(new { cstat = result.Status, cursor = result.Cursor, documentos = result.Documents });
            }
            catch (Exception error) when (error is ArgumentException or FormatException or InvalidDataException
                or CryptographicException or IOException or HttpRequestException
                || (error is OperationCanceledException && !token.IsCancellationRequested))
            {
                app.Logger.LogWarning("Falha na sincronização manual SEFAZ ({Type}).", error.GetType().Name);
                return Error("A sincronização falhou. Confira o certificado, a configuração e os logs do serviço.", 502);
            }
        }).RequireRateLimiting("sincronizar-web");
    }

    private static string[]? Recipients(string? list)
    {
        if (list is { Length: > 8000 }) return null;
        var parts = (list ?? "").Split([',', '\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (parts.Length > 30 || parts.Any(value => value.Length > 254 || !MailAddress.TryCreate(value, out var address)
            || !value.Equals(address.Address, StringComparison.OrdinalIgnoreCase)))
            return null;
        return parts.Select(value => value.ToLowerInvariant()).Distinct().ToArray();
    }

    private static IResult Error(string message, int code = 400) => Results.Json(new { erro = message }, statusCode: code);
    private static string Actor(HttpContext http) => http.User.Identity!.Name!;
    private sealed record MailInput(string? Email);
}
