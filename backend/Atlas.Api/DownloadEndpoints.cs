using System.Security.Claims;
using Microsoft.AspNetCore.Antiforgery;

internal static class DownloadEndpoints
{
    public static void MapDownloads(this WebApplication app)
    {
        var group = app.MapGroup("/api/downloads").RequireAuthorization();
        group.MapGet("", async (HttpContext http, DownloadQueue queue, CancellationToken token) =>
            Owner(http) is { } owner ? Results.Ok(await queue.ListAsync(owner, token)) : Results.Unauthorized());

        group.MapGet("/novos", async (HttpContext http, DownloadQueue queue, CancellationToken token) =>
            Owner(http) is { } owner ? Results.Ok(new { total = await queue.UnreadAsync(owner, token) }) : Results.Unauthorized());

        group.MapGet("/{id:guid}/arquivo", async (Guid id, HttpContext http, DownloadQueue queue, CancellationToken token) =>
            Owner(http) is { } owner ? await queue.FileAsync(owner, id, token) : Results.Unauthorized());

        group.MapPost("/{id:guid}/cancelar", async (Guid id, HttpContext http, IAntiforgery csrf,
            DownloadQueue queue, CancellationToken token) =>
        {
            if (Owner(http) is not { } owner) return Results.Unauthorized();
            if (!await ValidCsrf(http, csrf)) return SessionError();
            return await queue.CancelAsync(owner, id, token) ? Results.NoContent()
                : Results.NotFound(new { erro = "O pedido já foi concluído ou não está disponível." });
        });

        group.MapPost("/vistas", async (Guid[] ids, HttpContext http, IAntiforgery csrf,
            DownloadQueue queue, CancellationToken token) =>
        {
            if (Owner(http) is not { } owner) return Results.Unauthorized();
            if (!await ValidCsrf(http, csrf)) return SessionError();
            if (ids.Length is < 1 or > 25) return Results.BadRequest();
            await queue.SeenAsync(owner, ids, token);
            return Results.NoContent();
        });
    }

    internal static async Task<IResult> EnqueueAsync(HttpContext http, DownloadQueue queue,
        DownloadRequest request, CancellationToken token)
    {
        if (Owner(http) is not { } owner) return Results.Unauthorized();
        var id = await queue.EnqueueAsync(owner, request, token);
        return id.HasValue ? Results.Accepted("/downloads", new { id }) :
            Results.Json(new { erro = "A fila atingiu o limite. Aguarde a conclusão dos pedidos e tente de novo." }, statusCode: 429);
    }

    internal static Task<bool> ValidCsrf(HttpContext http, IAntiforgery csrf) =>
        http.Request.Headers.ContainsKey("X-CSRF-TOKEN") ? csrf.IsRequestValidAsync(http) : Task.FromResult(false);

    internal static IResult SessionError() => Results.BadRequest(new { erro = "Sessão expirada. Atualize a página." });

    private static string? Owner(HttpContext http) => http.User.FindFirstValue(ClaimTypes.NameIdentifier);
}
