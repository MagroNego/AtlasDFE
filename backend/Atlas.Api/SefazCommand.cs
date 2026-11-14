using System.Globalization;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Atlas.Infrastructure;
using Npgsql;

internal static class SefazCommand
{
    public static async Task<int> RunAsync(string[] args, IConfiguration settings, string connection, string cnpj)
    {
        using var cancellation = new CancellationTokenSource();
        ConsoleCancelEventHandler stop = (_, e) => { e.Cancel = true; cancellation.Cancel(); };
        Console.CancelKeyPress += stop;
        try
        {
            string uf = settings["Sefaz:Uf"] ?? throw new ArgumentException("Configure Sefaz__Uf.");
            var start = TimeOnly.ParseExact(settings["Sefaz:WindowStart"] ?? "22:00", "HH:mm", CultureInfo.InvariantCulture);
            var end = TimeOnly.ParseExact(settings["Sefaz:WindowEnd"] ?? "04:00", "HH:mm", CultureInfo.InvariantCulture);
            long? initial = ParseNsu(settings["Sefaz:InitialNsu"]);
            long? recovery = args.Length == 2 ? ParseNsu(args[1]) : null;
            using var certificate = Certificate(settings);
            using var client = SefazSync.Client(certificate);
            var result = await new SefazSync(connection, client).RunAsync(
                cnpj, uf, initial, recovery, DateTimeOffset.UtcNow, start, end, cancellation.Token);
            if (result is null)
            {
                Console.WriteLine("Nenhuma consulta enviada. Verifique a janela, o intervalo de espera e as execuções simultâneas.");
                return 0;
            }

            Console.WriteLine($"cStat {result.Status} | cursor {result.Cursor:D15} | documentos {result.Documents}");
            return result.Status is 137 or 138 ? 0 : 1;
        }
        catch (Exception error) when (error is ArgumentException or FormatException or InvalidDataException)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
        catch (Exception error) when (error is CryptographicException or IOException or NpgsqlException or HttpRequestException or OperationCanceledException)
        {
            Console.Error.WriteLine("Falha ao abrir o certificado, acessar os arquivos, gravar no PostgreSQL ou comunicar com a SEFAZ.");
            return 1;
        }
        finally
        {
            Console.CancelKeyPress -= stop;
        }
    }

    private static long? ParseNsu(string? text)
    {
        if (text is null)
            return null;
        if (text.Length is < 1 or > 15 || !text.All(char.IsAsciiDigit))
            throw new ArgumentException("Informe um NSU com até 15 dígitos.");
        return long.Parse(text, CultureInfo.InvariantCulture);
    }

    private static X509Certificate2 Certificate(IConfiguration settings)
    {
        if (settings["Sefaz:CertificatePath"] is { Length: > 0 } path)
        {
            var password = settings["Sefaz:CertificatePassword"];
            if (settings["Sefaz:CertificatePasswordFile"] is { Length: > 0 } file)
                password = File.ReadAllText(file).TrimEnd('\r', '\n');
            return X509CertificateLoader.LoadPkcs12FromFile(path, password, X509KeyStorageFlags.EphemeralKeySet);
        }

        if (!OperatingSystem.IsWindows() || settings["Sefaz:CertificateThumbprint"] is not { Length: > 0 } thumbprint)
            throw new ArgumentException("Configure um PFX ou a impressão digital de um certificado no Windows.");

        using var store = new X509Store(StoreName.My, StoreLocation.CurrentUser);
        store.Open(OpenFlags.ReadOnly);
        var matches = store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: false);
        if (matches.Count != 1)
        {
            foreach (var item in matches)
                item.Dispose();
            throw new ArgumentException("Certificado não localizado no repositório Pessoal do usuário do Windows.");
        }
        return matches[0];
    }
}
