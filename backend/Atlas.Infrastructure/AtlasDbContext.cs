using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace Atlas.Infrastructure;

public sealed class NotaConsulta
{
    public required string Cnpj { get; init; }
    public required string Chave { get; init; }
    public int Numero { get; init; }
    public int Serie { get; init; }
    public required JsonElement Detalhe { get; init; }
}

public sealed class ImportacaoConsulta
{
    public required string Cnpj { get; init; }
    public int Total { get; init; }
    public DateTime ImportadoEm { get; init; }
}

public sealed class AtlasDbContext(DbContextOptions<AtlasDbContext> options)
    : IdentityDbContext<IdentityUser>(options)
{
    public DbSet<NotaConsulta> Notas => Set<NotaConsulta>();
    public DbSet<ImportacaoConsulta> Importacoes => Set<ImportacaoConsulta>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);
        builder.Entity<NotaConsulta>(nota =>
        {
            nota.ToTable("consulta_notas");
            nota.HasKey(n => new { n.Cnpj, n.Chave });
            nota.HasIndex(n => new { n.Cnpj, n.Numero, n.Chave });
            nota.Property(n => n.Cnpj).HasColumnName("cnpj").HasMaxLength(14);
            nota.Property(n => n.Chave).HasColumnName("chave").HasMaxLength(44);
            nota.Property(n => n.Numero).HasColumnName("numero");
            nota.Property(n => n.Serie).HasColumnName("serie");
            nota.Property(n => n.Detalhe).HasColumnName("detalhe").HasColumnType("jsonb");
        });
        builder.Entity<ImportacaoConsulta>(importacao =>
        {
            importacao.ToTable("consulta_importacoes");
            importacao.HasKey(i => i.Cnpj);
            importacao.Property(i => i.Cnpj).HasColumnName("cnpj").HasMaxLength(14);
            importacao.Property(i => i.Total).HasColumnName("total");
            importacao.Property(i => i.ImportadoEm).HasColumnName("importado_em");
        });
    }
}
