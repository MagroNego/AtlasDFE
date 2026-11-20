using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Data.Sqlite;

internal static class DatabaseMigrationEndpoint
{
    private const int MaxDatabase = 60 * 1024 * 1024;
    private static readonly SemaphoreSlim UploadSlot = new(1);

    public static void MapDatabaseMigration(this WebApplication app, string connection, string cnpj)
    {
        var admin = app.MapGroup("/api/admin/migracao").RequireAuthorization(policy => policy.RequireRole(Administration.Role));
        admin.MapGet("", () => Results.Ok(new { cnpj, limiteMiB = MaxDatabase / 1024 / 1024 }));
        admin.MapPost("", async (HttpContext http, IAntiforgery csrf, CancellationToken token) =>
        {
            if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
            if (!http.Request.HasFormContentType) return Error("Envie uma cópia do banco SQLite.");
            if (http.Features.Get<IHttpMaxRequestBodySizeFeature>() is { IsReadOnly: false } limit)
                limit.MaxRequestBodySize = MaxDatabase + 64 * 1024;
            if (!await UploadSlot.WaitAsync(0, token)) return Error("Já existe uma migração em andamento.", 409);

            DirectoryInfo? temporary = null;
            try
            {
                var form = await http.Request.ReadFormAsync(token);
                if (form["confirmacao"] != "MIGRAR") return Error("Digite MIGRAR para confirmar a substituição do histórico.");
                if (form.Files.Count != 1 || form.Files.GetFile("arquivo") is not { Length: > 0 and <= MaxDatabase } file)
                    return Error("Selecione um único banco SQLite de até 60 MiB.");
                string password = form["senha"].ToString();
                if (password.Length > 256) return Error("A senha da cópia deve ter até 256 caracteres.");

                temporary = Directory.CreateTempSubdirectory("atlas-sqlite-");
                string path = Path.Combine(temporary.FullName, "origem.sqlite");
                await using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 65536, useAsync: true))
                    await file.CopyToAsync(output, token);

                int total = await LegacyHistoryImport.ImportAsync(connection, path, cnpj, password, token);
                return Results.Ok(new { total });
            }
            catch (SqliteException) { return Error("Não foi possível abrir o SQLite. Confira o arquivo, a senha e se é uma cópia independente do banco."); }
            catch (InvalidDataException error) { return Error(error.Message); }
            catch (BadHttpRequestException) { return Error("Envie um arquivo de até 60 MiB."); }
            finally
            {
                try { temporary?.Delete(recursive: true); }
                catch (IOException) { app.Logger.LogWarning("Não foi possível limpar a cópia temporária da migração."); }
                catch (UnauthorizedAccessException) { app.Logger.LogWarning("Não foi possível limpar a cópia temporária da migração."); }
                finally { UploadSlot.Release(); }
            }
        });
    }

    private static IResult Error(string message, int code = 400) =>
        Results.Json(new { erro = message }, statusCode: code);
}
