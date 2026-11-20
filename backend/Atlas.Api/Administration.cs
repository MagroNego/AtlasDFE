using Atlas.Infrastructure;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

internal static class Administration
{
    internal const string Role = "Administrador";
    internal const string Consulta = "Consulta";
    internal const string Fiscal = "Fiscal";
    internal const string Export = "Exportar";

    private static string? Profile(string? requested, bool admin) => requested switch
    {
        null => admin ? Role : Fiscal,
        "administrador" => Role,
        "consulta" => Consulta,
        "fiscal" => Fiscal,
        _ => null,
    };


    internal static void MapAccounts(this RouteGroupBuilder group, string cnpj)
    {
        group.MapGet("/contas", async (UserManager<IdentityUser> users, CancellationToken token) =>
        {
            var accounts = await users.Users.OrderBy(user => user.Email).Take(501).ToListAsync(token);
            var elevated = await users.GetUsersInRoleAsync(Role);
            var admins = elevated.Select(user => user.Id).ToHashSet(StringComparer.Ordinal);
            var readers = (await users.GetUsersInRoleAsync(Consulta)).Select(user => user.Id).ToHashSet(StringComparer.Ordinal);
            return Results.Ok(accounts.Take(500).Select(user => new
            {
                user.Email, user.UserName, Admin = admins.Contains(user.Id),
                Perfil = admins.Contains(user.Id) ? "administrador" : readers.Contains(user.Id) ? "consulta" : "fiscal",
                Ativa = user.LockoutEnd != DateTimeOffset.MaxValue,
            }));
        });
        group.MapPost("/contas", async (AccountInput input, HttpContext http, IAntiforgery csrf,
            UserManager<IdentityUser> users, RoleManager<IdentityRole> roles, AtlasDbContext db, CancellationToken token) =>
        {
            if (!await csrf.IsRequestValidAsync(http))
                return Results.BadRequest(new { erro = "Sessão expirada." });
            var email = input.Email?.Trim();
            if (!ValidEmail(email))
                return Results.BadRequest(new { erro = "Informe um e-mail válido." });
            var profile = Profile(input.Perfil, input.Admin);
            if (profile is null)
                return Results.BadRequest(new { erro = "Informe um perfil válido." });

            await using var transaction = await db.Database.BeginTransactionAsync(token);
            await LockAsync(db, token);
            var account = await users.FindByEmailAsync(email!);
            bool self = account is not null && account.Id == users.GetUserId(http.User);
            bool isAdmin = account is not null && await users.IsInRoleAsync(account, Role);
            bool removeAdmin = input.Acao is "bloquear" or "excluir" || input.Acao == "editar" && profile != Role;
            if (account is not null && removeAdmin && (self || isAdmin &&
                !(await users.GetUsersInRoleAsync(Role)).Any(other => other.Id != account.Id &&
                    other.LockoutEnd.GetValueOrDefault() <= DateTimeOffset.UtcNow)))
                return Results.BadRequest(new { erro = "Mantenha outro administrador ativo. Você não pode retirar seu próprio acesso." });
            IdentityResult result;
            switch (input.Acao)
            {
                case "criar" when account is null && input.Senha is { Length: >= 12 and <= 128 }:
                    if (!ValidUserName(input.Usuario))
                        return Results.BadRequest(new { erro = "Use letras, números, ponto, hífen ou sublinhado no usuário." });
                    account = new IdentityUser { UserName = input.Usuario?.Trim() is { Length: > 0 } name ? name : email,
                        Email = email, LockoutEnabled = true };
                    result = await users.CreateAsync(account, input.Senha);
                    if (result.Succeeded) result = await SetRoleAsync(users, roles, account, profile);
                    break;
                case "editar" when account is not null:
                    string? newEmail = input.NovoEmail?.Trim();
                    if (!ValidEmail(newEmail) || !ValidUserName(input.Usuario))
                        return Results.BadRequest(new { erro = "Confira o novo e-mail e o nome de usuário." });
                    result = await EditIdentityAsync(users, account, newEmail!, input.Usuario);
                    if (result.Succeeded) result = await SetRoleAsync(users, roles, account, profile);
                    if (result.Succeeded) result = await users.UpdateSecurityStampAsync(account);
                    break;
                case "excluir" when account is not null && !self:
                    result = await users.DeleteAsync(account);
                    break;
                case "senha" when account is not null && input.Senha is { Length: >= 12 and <= 128 }:
                    result = await users.ResetPasswordAsync(account, await users.GeneratePasswordResetTokenAsync(account), input.Senha);
                    break;
                case "bloquear" or "liberar" when account is not null && account.Id != users.GetUserId(http.User):
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
            await AuditAsync(db, cnpj, http.User.Identity!.Name!, $"conta.{input.Acao}", input.Acao == "editar" ? $"{email} → {input.NovoEmail?.Trim()}" : email!, token);
            await transaction.CommitAsync(token);
            return Results.NoContent();
        });
        group.MapGet("/auditoria", async (AtlasDbContext db, CancellationToken token) =>
            Results.Ok(await db.Database.SqlQuery<AuditRow>($"""
                SELECT usuario AS "Usuario",acao AS "Acao",alvo AS "Alvo",criado_em AS "CriadoEm"
                FROM atlas_auditoria WHERE cnpj={cnpj} ORDER BY id DESC LIMIT 100
                """).ToListAsync(token)));
    }

    internal static bool ValidEmail(string? email) => email is { Length: > 0 and <= 254 }
        && System.Net.Mail.MailAddress.TryCreate(email, out var address) && address.Address.Equals(email, StringComparison.OrdinalIgnoreCase);

    internal static bool ValidUserName(string? name) => string.IsNullOrWhiteSpace(name) ||
        name.Trim() is { Length: >= 3 and <= 60 } value &&
        value.All(ch => char.IsAsciiLetterOrDigit(ch) || ch is '.' or '_' or '-');

    internal static async Task<IdentityResult> EditIdentityAsync(UserManager<IdentityUser> users,
        IdentityUser account, string email, string? name)
    {
        string username = name?.Trim() is { Length: > 0 } custom ? custom : email;
        if (!string.Equals(account.UserName, username, StringComparison.Ordinal))
        {
            var changed = await users.SetUserNameAsync(account, username);
            if (!changed.Succeeded) return changed;
        }
        return string.Equals(account.Email, email, StringComparison.Ordinal)
            ? IdentityResult.Success : await users.SetEmailAsync(account, email);
    }

    internal static async Task<IdentityResult> SetRoleAsync(UserManager<IdentityUser> users,
        RoleManager<IdentityRole> roles, IdentityUser account, string profile)
    {
        if (!await roles.RoleExistsAsync(profile))
        {
            var created = await roles.CreateAsync(new IdentityRole(profile));
            if (!created.Succeeded) return created;
        }
        var current = await users.GetRolesAsync(account);
        var remove = current.Where(role => role != profile && role is Role or Consulta or Fiscal).ToArray();
        if (remove.Length > 0)
        {
            var removed = await users.RemoveFromRolesAsync(account, remove);
            if (!removed.Succeeded) return removed;
        }
        return current.Contains(profile) ? IdentityResult.Success : await users.AddToRoleAsync(account, profile);
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

    private sealed record AccountInput(string? Email, string? Acao, string? Senha, bool Admin, string? Perfil, string? NovoEmail, string? Usuario);
    private sealed record AuditRow(string Usuario, string Acao, string Alvo, DateTime CriadoEm);
}
