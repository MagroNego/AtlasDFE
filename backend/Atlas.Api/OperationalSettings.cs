using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Atlas.Infrastructure;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Npgsql;

internal sealed class OperationalSettings(string connection, string cnpj, IConfiguration defaults, IDataProtectionProvider protection)
{
    private readonly IDataProtector protector = protection.CreateProtector("AtlasDFE.Sefaz.Pfx.v1", cnpj);
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public async Task<Values> ReadAsync(CancellationToken token)
    {
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync(token);
        await using var command = new NpgsqlCommand("SELECT tipo,dados::text FROM atlas_operacao WHERE cnpj=$1", db);
        command.Parameters.AddWithValue(cnpj);
        var overlay = new Dictionary<string, string?>();
        CertificateInfo? certificate = null;
        await using var rows = await command.ExecuteReaderAsync(token);
        while (await rows.ReadAsync(token))
        {
            var data = rows.GetString(1);
            switch (rows.GetString(0))
            {
                case "agenda":
                    var agenda = JsonSerializer.Deserialize<AgendaInput>(data, Json)!;
                    if (!string.IsNullOrEmpty(agenda.Uf)) overlay["Sefaz:Uf"] = agenda.Uf;
                    overlay["Sefaz:ScheduleEnabled"] = agenda.Habilitada.ToString();
                    overlay["Sefaz:WindowStart"] = agenda.Inicio;
                    overlay["Sefaz:WindowEnd"] = agenda.Fim;
                    overlay["Sefaz:ScheduleTimes"] = agenda.Horarios;
                    overlay["Sefaz:ScheduleDays"] = string.Join(',', agenda.Dias!);
                    break;
                case "alertas":
                    var alerts = JsonSerializer.Deserialize<AlertsInput>(data, Json)!;
                    overlay["Smtp:Enabled"] = alerts.Habilitados.ToString();
                    overlay["Smtp:Recipients"] = alerts.Destinatarios;
                    break;
                case "certificado":
                    certificate = JsonSerializer.Deserialize<CertificateInfo>(data, Json);
                    break;
            }
        }
        return new(new ConfigurationBuilder().AddConfiguration(defaults).AddInMemoryCollection(overlay).Build(), certificate);
    }

    public async Task<X509Certificate2> CertificateAsync(CancellationToken token)
    {
        byte[]? secret;
        await using (var db = new NpgsqlConnection(connection))
        {
            await db.OpenAsync(token);
            await using var command = new NpgsqlCommand(
                "SELECT segredo FROM atlas_operacao WHERE cnpj=$1 AND tipo='certificado'", db);
            command.Parameters.AddWithValue(cnpj);
            secret = (byte[]?)await command.ExecuteScalarAsync(token);
        }
        if (secret is null) return SefazCommand.Certificate(defaults);

        byte[] clear = protector.Unprotect(secret);
        CertificateBundle? bundle = null;
        try
        {
            bundle = JsonSerializer.Deserialize<CertificateBundle>(clear, Json)
                ?? throw new CryptographicException("Certificado protegido inválido.");
            return X509CertificateLoader.LoadPkcs12(bundle.Pfx, bundle.Password, X509KeyStorageFlags.EphemeralKeySet);
        }
        finally
        {
            if (bundle is not null) CryptographicOperations.ZeroMemory(bundle.Pfx);
            CryptographicOperations.ZeroMemory(clear);
        }
    }

    public byte[] Protect(byte[] pfx, string password)
    {
        var clear = JsonSerializer.SerializeToUtf8Bytes(new CertificateBundle(pfx, password), Json);
        try { return protector.Protect(clear); }
        finally { CryptographicOperations.ZeroMemory(clear); }
    }

    private sealed record CertificateBundle(byte[] Pfx, string Password);

    public async Task SaveAsync<T>(AtlasDbContext db, string actor, string kind, T value, byte[]? secret, CancellationToken token)
    {
        string json = JsonSerializer.Serialize(value, Json);
        await using var tx = await db.Database.BeginTransactionAsync(token);
        await db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO atlas_operacao(cnpj,tipo,dados,segredo) VALUES ({cnpj},{kind},CAST({json} AS jsonb),{secret})
            ON CONFLICT (cnpj,tipo) DO UPDATE SET dados=excluded.dados, segredo=excluded.segredo
            """, token);
        await Administration.AuditAsync(db, cnpj, actor, $"operacao.{kind}", "", token);
        await tx.CommitAsync(token);
    }

    public async Task RemoveCertificateAsync(AtlasDbContext db, string actor, CancellationToken token)
    {
        await using var tx = await db.Database.BeginTransactionAsync(token);
        await db.Database.ExecuteSqlInterpolatedAsync(
            $"DELETE FROM atlas_operacao WHERE cnpj={cnpj} AND tipo='certificado'", token);
        await Administration.AuditAsync(db, cnpj, actor, "operacao.certificado.remover", "", token);
        await tx.CommitAsync(token);
    }

    internal sealed record Values(IConfigurationRoot Configuration, CertificateInfo? Certificate) : IDisposable
    {
        public void Dispose() => (Configuration as IDisposable)?.Dispose();

        public bool HasCertificate => Certificate is not null
            || !string.IsNullOrWhiteSpace(Configuration["Sefaz:CertificatePath"])
            || (OperatingSystem.IsWindows() && !string.IsNullOrWhiteSpace(Configuration["Sefaz:CertificateThumbprint"]));
    }
    internal static bool ValidUf(string? uf) => uf is "11" or "12" or "13" or "14" or "15" or "16" or "17"
        or "21" or "22" or "23" or "24" or "25" or "26" or "27" or "28" or "29"
        or "31" or "32" or "33" or "35" or "41" or "42" or "43" or "50" or "51" or "52" or "53";

    internal sealed record AgendaInput(bool Habilitada, string? Inicio, string? Fim, string? Horarios, int[]? Dias, string? Uf = null);
    internal sealed record AlertsInput(bool Habilitados, string? Destinatarios);
    internal sealed record CertificateInfo(string ImpressaoDigital, DateTime ValidoDesde, DateTime ValidoAte);
}
