using MailKit.Net.Smtp;
using MailKit.Security;
using MimeKit;
using Npgsql;

internal sealed class SmtpAlerts(string connection, string cnpj, IConfiguration configuration, ILogger<SmtpAlerts> log) : BackgroundService
{
    internal static bool Enabled(IConfiguration settings) => settings.GetValue<bool>("Smtp:Enabled");

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(1));
        do
        {
            try { await DeliverAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { return; }
            catch (Exception error) { log.LogWarning("Não foi possível processar alertas SMTP ({Type}).", error.GetType().Name); }
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task DeliverAsync(CancellationToken token)
    {
        var recipients = (configuration["Smtp:Recipients"] ?? "").Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries)
            .Select(value => MailboxAddress.Parse(value).Address.ToLowerInvariant()).Distinct().Take(30).ToArray();
        if (recipients.Length == 0)
            return;

        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var tx = await db.BeginTransactionAsync(token);
        await using (var guard = new NpgsqlCommand("SELECT pg_try_advisory_xact_lock(718323, hashtext($1))", db, tx))
        {
            guard.Parameters.AddWithValue(cnpj);
            if ((bool?)await guard.ExecuteScalarAsync(token) != true)
                return;
        }
        await using (var prepare = new NpgsqlCommand("""
            INSERT INTO atlas_alertas_entregas(cnpj,nsu,email)
            SELECT a.cnpj,a.nsu,r.email FROM atlas_alertas a CROSS JOIN unnest($2::text[]) AS r(email)
            WHERE a.cnpj=$1 ON CONFLICT DO NOTHING
            """, db, tx))
        {
            prepare.Parameters.AddWithValue(cnpj);
            prepare.Parameters.AddWithValue(recipients);
            await prepare.ExecuteNonQueryAsync(token);
        }
        var pending = new List<(long Nsu, string Key, string Mail)>();
        await using (var select = new NpgsqlCommand("""
            SELECT e.nsu,a.chave,e.email FROM atlas_alertas_entregas e JOIN atlas_alertas a USING(cnpj,nsu)
            WHERE e.cnpj=$1 AND e.email=ANY($2) AND enviado_em IS NULL AND tentar_em <= now()
            ORDER BY tentar_em,nsu,email LIMIT 1
            """, db, tx))
        {
            select.Parameters.AddWithValue(cnpj);
            select.Parameters.AddWithValue(recipients);
            await using var rows = await select.ExecuteReaderAsync(token);
            while (await rows.ReadAsync(token)) pending.Add((rows.GetInt64(0), rows.GetString(1), rows.GetString(2)));
        }

        foreach (var item in pending)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
            timeout.CancelAfter(TimeSpan.FromSeconds(20));
            bool sent = false;
            try
            {
                await SendAsync(configuration, item.Key, item.Mail, timeout.Token);
                sent = true;
            }
            catch (Exception error) when (!token.IsCancellationRequested)
            {
                log.LogWarning("Falha SMTP no NSU {Nsu} ({Type}).", item.Nsu, error.GetType().Name);
            }
            await using var mark = new NpgsqlCommand("""
                UPDATE atlas_alertas_entregas SET tentativas=tentativas+1,
                    enviado_em=CASE WHEN $4 THEN now() ELSE NULL END,
                    tentar_em=now()+interval '15 minutes'
                WHERE cnpj=$1 AND nsu=$2 AND email=$3
                """, db, tx);
            foreach (var value in new object[] { cnpj, item.Nsu, item.Mail, sent })
                mark.Parameters.AddWithValue(value);
            await mark.ExecuteNonQueryAsync(token);

        }
        await tx.CommitAsync(token);
    }

    private static async Task SendAsync(IConfiguration settings, string key, string recipient, CancellationToken token)
    {
        string host = settings["Smtp:Host"] ?? throw new InvalidOperationException("Configure Smtp__Host.");
        var security = settings["Smtp:Security"] switch
        {
            "ssl" => SecureSocketOptions.SslOnConnect,
            null or "starttls" => SecureSocketOptions.StartTls,
            _ => throw new InvalidOperationException("Use ssl ou starttls em Smtp__Security."),
        };
        var mail = new MimeMessage();
        mail.From.Add(MailboxAddress.Parse(settings["Smtp:From"] ?? ""));
        mail.To.Add(MailboxAddress.Parse(recipient));
        mail.Subject = "Atlas DFE | Operação não realizada";
        mail.Body = new TextPart("plain") { Text = $"A SEFAZ registrou Operação não Realizada para a NF-e {key}." };
        using var client = new SmtpClient();
        await client.ConnectAsync(host, settings.GetValue<int?>("Smtp:Port") ?? (security == SecureSocketOptions.SslOnConnect ? 465 : 587), security, token);
        if (settings["Smtp:User"] is { Length: > 0 } user)
        {
            string file = settings["Smtp:PasswordFile"] ?? throw new InvalidOperationException("Configure Smtp__PasswordFile.");
            string password = (await File.ReadAllTextAsync(file, token)).TrimEnd('\r', '\n');
            await client.AuthenticateAsync(user, password, token);
        }
        await client.SendAsync(mail, token);
        await client.DisconnectAsync(true, token);
    }
}
