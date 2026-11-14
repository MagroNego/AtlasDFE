using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

internal static class Administration
{
    internal const string Role = "Administrador";

    internal static void MapAccounts(this RouteGroupBuilder group, string cnpj)
    {
        group.MapGet("/contas", async (UserManager<IdentityUser> users, CancellationToken token) =>
        {
            var accounts = await users.Users.OrderBy(user => user.Email).Take(501).ToListAsync(token);
            var elevated = await users.GetUsersInRoleAsync(Role);
            var admins = elevated.Select(user => user.Id).ToHashSet(StringComparer.Ordinal);
            return Results.Ok(accounts.Take(500).Select(user => new
            {
                user.Email, Admin = admins.Contains(user.Id),
                Ativa = user.LockoutEnd != DateTimeOffset.MaxValue,
            }));
        });
        group.MapPost("/contas", async (AccountInput input, HttpContext http, IAntiforgery csrf,
            UserManager<IdentityUser> users, RoleManager<IdentityRole> roles, AtlasDbContext db, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada." });
            var email = input.Email?.Trim();
            if (string.IsNullOrEmpty(email) || email.Length > 254 || !System.Net.Mail.MailAddress.TryCreate(email, out var address)
                || !address.Address.Equals(email, StringComparison.OrdinalIgnoreCase))
                return Results.BadRequest(new { erro = "Informe um e-mail válido." });

            await using var transaction = await db.Database.BeginTransactionAsync(token);
            await LockAsync(db, token);
            var account = await users.FindByEmailAsync(email);
            IdentityResult result;
            switch (input.Acao)
            {
                case "criar" when account is null && input.Senha is { Length: >= 12 and <= 128 }:
                    account = new IdentityUser { UserName = email, Email = email, LockoutEnabled = true };
                    result = await users.CreateAsync(account, input.Senha);
                    if (result.Succeeded && input.Admin)
                        result = await users.AddToRoleAsync(account, Role);
                    break;
                case "senha" when account is not null && input.Senha is { Length: >= 12 and <= 128 }:
                    result = await users.ResetPasswordAsync(account, await users.GeneratePasswordResetTokenAsync(account), input.Senha);
                    break;
                case "bloquear" or "liberar" when account is not null && account.Id != users.GetUserId(http.User):
                    if (input.Acao == "bloquear" && await users.IsInRoleAsync(account, Role) &&
                        !(await users.GetUsersInRoleAsync(Role)).Any(other => other.Id != account.Id && other.LockoutEnd != DateTimeOffset.MaxValue))
                        return Results.BadRequest(new { erro = "Mantenha pelo menos um administrador ativo." });
                    result = await users.SetLockoutEndDateAsync(account,
                        input.Acao == "bloquear" ? DateTimeOffset.MaxValue : null);
                    if (result.Succeeded)
                        result = await users.UpdateSecurityStampAsync(account);
                    break;
                default:
                    return Results.BadRequest(new { erro = "Verifique a conta, a ação e a senha de 12 a 128 caracteres." });
            }
            if (!result.Succeeded)
                return Results.BadRequest(new { erro = string.Join(" ", result.Errors.Select(error => error.Description)) });
            await AuditAsync(db, cnpj, http.User.Identity!.Name!, $"conta.{input.Acao}", email, token);
            await transaction.CommitAsync(token);
            return Results.NoContent();
        });
        group.MapGet("/auditoria", async (AtlasDbContext db, CancellationToken token) =>
            Results.Ok(await db.Database.SqlQuery<AuditRow>($"""
                SELECT usuario AS "Usuario",acao AS "Acao",alvo AS "Alvo",criado_em AS "CriadoEm"
                FROM atlas_auditoria WHERE cnpj={cnpj} ORDER BY id DESC LIMIT 100
                """).ToListAsync(token)));
    }

    internal static Task LockAsync(AtlasDbContext db, CancellationToken token) =>
        db.Database.ExecuteSqlRawAsync("SELECT pg_advisory_xact_lock(718320, 0)", token);

    internal static Task AuditAsync(AtlasDbContext db, string cnpj, string user, string action, string target, CancellationToken token) =>
        db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO atlas_auditoria(cnpj,usuario,acao,alvo) VALUES ({cnpj},{user},{action},{target})
            """, token);

    internal static async Task AssignAdminAsync(IServiceProvider services, IdentityUser user)
    {
        var roles = services.GetRequiredService<RoleManager<IdentityRole>>();
        if (!await roles.RoleExistsAsync(Role))
            Ensure(await roles.CreateAsync(new IdentityRole(Role)));
        var users = services.GetRequiredService<UserManager<IdentityUser>>();
        if (!await users.IsInRoleAsync(user, Role))
        {
            Ensure(await users.AddToRoleAsync(user, Role));
            Ensure(await users.UpdateSecurityStampAsync(user));
        }
    }

    internal static void Ensure(IdentityResult result)
    {
        if (!result.Succeeded)
            throw new InvalidOperationException(string.Join(" ", result.Errors.Select(e => e.Description)));
    }

    private sealed record AccountInput(string? Email, string? Acao, string? Senha, bool Admin);
    private sealed record AuditRow(string Usuario, string Acao, string Alvo, DateTime CriadoEm);
}
