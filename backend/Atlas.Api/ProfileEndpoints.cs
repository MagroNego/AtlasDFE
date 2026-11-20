using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

internal static class ProfileEndpoints
{
    public static void MapProfile(this WebApplication app, string cnpj)
    {
        app.MapPost("/api/cadastro", async (Registration input, HttpContext http, IAntiforgery csrf, IConfiguration configuration,
            UserManager<IdentityUser> users, RoleManager<IdentityRole> roles, AtlasDbContext db, CancellationToken token) =>
        {
            if (!SelfRegistration(configuration)) return Results.NotFound();
            if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
            string? email = input.Email?.Trim();
            if (!Administration.ValidEmail(email) || !Administration.ValidUserName(input.Usuario)
                || input.Senha is not { Length: >= 12 and <= 128 })
                return Results.BadRequest(new { erro = "Confira o e-mail, usuário e senha de 12 a 128 caracteres." });
            await using var tx = await db.Database.BeginTransactionAsync(token);
            await Administration.LockAsync(db, token);
            if (await users.FindByEmailAsync(email!) is not null)
                return Results.BadRequest(new { erro = "Não foi possível solicitar a conta. Consulte a administração." });
            var account = new IdentityUser
            {
                UserName = input.Usuario?.Trim() is { Length: > 0 } name ? name : email,
                Email = email, LockoutEnabled = true, LockoutEnd = DateTimeOffset.MaxValue,
            };
            var result = await users.CreateAsync(account, input.Senha);
            if (result.Succeeded) result = await Administration.SetRoleAsync(users, roles, account, Administration.Consulta);
            if (!result.Succeeded) return Results.BadRequest(new { erro = "Não foi possível solicitar a conta. Confira os dados." });
            await Administration.AuditAsync(db, cnpj, email!, "conta.solicitar", email!, token);
            await tx.CommitAsync(token);
            return Results.NoContent();
        }).AllowAnonymous().RequireRateLimiting("login");

        app.MapGet("/api/perfil", async (HttpContext http, UserManager<IdentityUser> users) =>
        {
            var account = await users.GetUserAsync(http.User);
            return account is null ? Results.Unauthorized() : Results.Ok(new
            {
                email = account.Email,
                usuario = account.UserName == account.Email ? "" : account.UserName,
            });
        }).RequireAuthorization();

        app.MapPut("/api/perfil", async (ProfileInput input, HttpContext http, IAntiforgery csrf,
            UserManager<IdentityUser> users, SignInManager<IdentityUser> login, AtlasDbContext db, CancellationToken token) =>
        {
            if (!await DownloadEndpoints.ValidCsrf(http, csrf)) return DownloadEndpoints.SessionError();
            string? email = input.Email?.Trim();
            if (!Administration.ValidEmail(email) || !Administration.ValidUserName(input.Usuario)
                || input.SenhaAtual is not { Length: > 0 and <= 128 }
                || input.NovaSenha is { Length: > 0 and (< 12 or > 128) })
                return Results.BadRequest(new { erro = "Confira os dados do perfil e a senha atual." });

            await using var tx = await db.Database.BeginTransactionAsync(token);
            await Administration.LockAsync(db, token);
            var account = await users.GetUserAsync(http.User);
            if (account is null) return Results.Unauthorized();
            if (!(await login.CheckPasswordSignInAsync(account, input.SenhaAtual, lockoutOnFailure: true)).Succeeded)
            {
                await tx.CommitAsync(token);
                return Results.BadRequest(new { erro = "Não foi possível confirmar a senha atual." });
            }
            var result = await Administration.EditIdentityAsync(users, account, email!, input.Usuario);
            if (result.Succeeded && input.NovaSenha is { Length: > 0 })
                result = await users.ChangePasswordAsync(account, input.SenhaAtual, input.NovaSenha);
            if (result.Succeeded) result = await users.UpdateSecurityStampAsync(account);
            if (!result.Succeeded) return Results.BadRequest(new { erro = string.Join(" ", result.Errors.Select(e => e.Description)) });
            await Administration.AuditAsync(db, cnpj, http.User.Identity!.Name!, "conta.perfil", email!, token);
            await tx.CommitAsync(token);
            await login.RefreshSignInAsync(account);
            return Results.NoContent();
        }).RequireAuthorization();
    }

    internal static bool SelfRegistration(IConfiguration settings) =>
        bool.TryParse(settings["Atlas:SelfRegistration"], out bool enabled) && enabled;

    private sealed record Registration(string? Email, string? Usuario, string? Senha);
    private sealed record ProfileInput(string? Email, string? Usuario, string? SenhaAtual, string? NovaSenha);
}
