using System.Data;
using Microsoft.AspNetCore.RateLimiting;
using Atlas.Domain;
using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Npgsql;

var importacaoLegada = args is ["--importar-historico", _];
var builder = WebApplication.CreateBuilder(importacaoLegada ? [] : args);
var configuration = builder.Configuration;
var connectionString = configuration.GetConnectionString("Database");
if (string.IsNullOrWhiteSpace(connectionString))
{
    var passwordFile = configuration["Database:PasswordFile"]
        ?? throw new InvalidOperationException("Configure a conexão PostgreSQL.");
    connectionString = new NpgsqlConnectionStringBuilder
    {
        Host = configuration["Database:Host"] ?? "db",
        Database = "atlas",
        Username = "atlas",
        Password = File.ReadAllText(passwordFile).Trim(),
    }.ConnectionString;
}

builder.Services.AddDbContext<AtlasDbContext>(options => options.UseNpgsql(connectionString));
builder.Services.AddIdentity<IdentityUser, IdentityRole>(options =>
{
    options.Password.RequiredLength = 12;
    options.User.RequireUniqueEmail = true;
})
.AddEntityFrameworkStores<AtlasDbContext>();
builder.Services.AddAuthorization();
builder.Services.AddAntiforgery(options => options.HeaderName = "X-CSRF-TOKEN");
builder.Services.ConfigureApplicationCookie(options =>
{
    options.Cookie.Name = "Atlas.Session";
    options.Cookie.HttpOnly = true;
    options.Events.OnRedirectToLogin = context =>
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        return Task.CompletedTask;
    };
    options.Cookie.SecurePolicy = builder.Environment.IsDevelopment()
        ? CookieSecurePolicy.SameAsRequest : CookieSecurePolicy.Always;
});
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.AddFixedWindowLimiter("login", limit =>
    {
        limit.PermitLimit = 20;
        limit.Window = TimeSpan.FromMinutes(1);
    });
});
var dataProtection = builder.Services.AddDataProtection().SetApplicationName("AtlasDFE");
if (configuration["DataProtection:Path"] is { Length: > 0 } keyPath)
    dataProtection.PersistKeysToFileSystem(new DirectoryInfo(keyPath));

var app = builder.Build();
if (args is ["--migrate"] or ["--bootstrap-admin"])
{
    await using var scope = app.Services.CreateAsyncScope();
    if (args[0] == "--migrate")
        await scope.ServiceProvider.GetRequiredService<AtlasDbContext>().Database.MigrateAsync();
    else
        await CriarAdministrador(scope.ServiceProvider, configuration);
    return;
}

var cnpj = configuration["Atlas:Cnpj"];
if (cnpj is not { Length: 14 } || !cnpj.All(char.IsAsciiDigit))
    throw new InvalidOperationException("Configure Atlas__Cnpj com os 14 dígitos do CNPJ.");

if (importacaoLegada)
{
    using var cancellation = new CancellationTokenSource();
    ConsoleCancelEventHandler stop = (_, eventArgs) => { eventArgs.Cancel = true; cancellation.Cancel(); };
    Console.CancelKeyPress += stop;
    try
    {
        var password = configuration["Atlas:LegacyPassword"];
        var passwordFile = configuration["Atlas:LegacyPasswordFile"];
        if (!string.IsNullOrEmpty(passwordFile))
            password = File.ReadAllText(passwordFile).TrimEnd('\r', '\n');
        int total = await LegacyHistoryImport.ImportAsync(connectionString, args[1], cnpj, password, cancellation.Token);
        Console.WriteLine($"{total} notas publicadas na Consulta.");
    }
    catch (InvalidDataException error)
    {
        Console.Error.WriteLine(error.Message);
        Environment.ExitCode = 1;
    }
    catch (Microsoft.Data.Sqlite.SqliteException)
    {
        Console.Error.WriteLine("Não foi possível ler o SQLite/SQLCipher. Verifique a origem, a senha e o acesso ao arquivo.");
        Environment.ExitCode = 1;
    }
    catch (NpgsqlException)
    {
        Console.Error.WriteLine("Não foi possível publicar o histórico. Verifique a conexão e as migrações do PostgreSQL.");
        Environment.ExitCode = 1;
    }
    catch (IOException)
    {
        Console.Error.WriteLine("Não foi possível ler o arquivo da origem ou da senha.");
        Environment.ExitCode = 1;
    }
    catch (OperationCanceledException)
    {
        Console.Error.WriteLine("Importação cancelada.");
        Environment.ExitCode = 1;
    }
    finally
    {
        Console.CancelKeyPress -= stop;
    }
    return;
}

app.UseExceptionHandler("/erro");
app.UseStatusCodePages();
app.Use(async (http, next) =>
{
    if (http.Request.Path.StartsWithSegments("/api"))
        http.Response.Headers.CacheControl = "no-store";
    await next();
});

app.UseAuthentication();
app.UseAuthorization();
app.UseRateLimiter();

app.Map("/erro", () => Results.Problem());
app.MapGet("/health", () => Results.Ok()).AllowAnonymous();
app.MapGet("/api/sessao", (HttpContext http, IAntiforgery csrf) =>
    Results.Ok(new { autenticado = http.User.Identity?.IsAuthenticated == true,
        token = csrf.GetAndStoreTokens(http).RequestToken })).AllowAnonymous();
app.MapPost("/api/login", async (
    Credenciais input, HttpContext http, IAntiforgery csrf, SignInManager<IdentityUser> login) =>
{
    if (!await csrf.IsRequestValidAsync(http))
        return Results.BadRequest();
    if (string.IsNullOrWhiteSpace(input.Email) || string.IsNullOrEmpty(input.Senha))
        return Results.BadRequest();
    var resultado = await login.PasswordSignInAsync(input.Email.Trim(), input.Senha, false, true);
    return resultado.Succeeded ? Results.NoContent() : Results.Unauthorized();
}).AllowAnonymous().RequireRateLimiting("login");

app.MapPost("/api/logout", async (HttpContext http, IAntiforgery csrf, SignInManager<IdentityUser> login) =>
{
    if (!await csrf.IsRequestValidAsync(http))
        return Results.BadRequest();
    await login.SignOutAsync();
    return Results.NoContent();
}).RequireAuthorization();

app.MapGet("/api/consulta", async (
    string? busca, int? pagina, AtlasDbContext banco, CancellationToken cancelamento) =>
{
    var nota = IdentificadorNota.Parse(busca);
    if (nota is null || pagina is < 1 or > 10000)
        return Results.BadRequest(new { erro = "Informe uma chave de NF-e ou um número de nota válido." });

    await using var leitura = await banco.Database.BeginTransactionAsync(IsolationLevel.RepeatableRead, cancelamento);
    var importacao = await banco.Importacoes.AsNoTracking()
        .SingleOrDefaultAsync(i => i.Cnpj == cnpj, cancelamento);
    if (importacao is null)
        return Results.Ok(new { importadoEm = (DateTime?)null, notas = Array.Empty<object>(), mais = false });

    const int tamanho = 25;
    var consulta = banco.Notas.AsNoTracking().Where(n => n.Cnpj == cnpj);
    consulta = nota.Chave is not null
        ? consulta.Where(n => n.Chave == nota.Chave)
        : consulta.Where(n => n.Numero == nota.Numero);
    var linhas = await consulta.OrderBy(n => n.Chave)
        .Skip(((pagina ?? 1) - 1) * tamanho).Take(tamanho + 1)
        .Select(n => new { n.Chave, n.Numero, n.Serie, n.Detalhe })
        .ToListAsync(cancelamento);

    return Results.Ok(new { importacao.ImportadoEm, notas = linhas.Take(tamanho), mais = linhas.Count > tamanho });
}).RequireAuthorization();

await app.RunAsync();

static async Task CriarAdministrador(IServiceProvider services, IConfiguration configuration)
{
    var users = services.GetRequiredService<UserManager<IdentityUser>>();
    var banco = services.GetRequiredService<AtlasDbContext>();
    await using var transaction = await banco.Database.BeginTransactionAsync();
    await banco.Database.ExecuteSqlRawAsync("SELECT pg_advisory_xact_lock(718320, 0)");
    if (await users.Users.AnyAsync())
        throw new InvalidOperationException("Já existe uma conta neste banco.");
    var email = configuration["Bootstrap:Email"]?.Trim();
    var senha = configuration["Bootstrap:Password"];
    if (string.IsNullOrWhiteSpace(email) || string.IsNullOrEmpty(senha))
        throw new InvalidOperationException("Configure Bootstrap__Email e Bootstrap__Password.");
    var resultado = await users.CreateAsync(new IdentityUser { UserName = email, Email = email }, senha);
    if (!resultado.Succeeded)
        throw new InvalidOperationException(string.Join(" ", resultado.Errors.Select(e => e.Description)));
    await transaction.CommitAsync();
}

internal sealed record Credenciais(string? Email, string? Senha);
