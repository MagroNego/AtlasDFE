using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261120210000_Downloads")]
public sealed class DownloadsMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE atlas_downloads (
            id uuid PRIMARY KEY,
            cnpj varchar(14) NOT NULL,
            usuario text NOT NULL,
            tipo text NOT NULL,
            pedido jsonb,
            estado text NOT NULL CHECK (estado IN ('na_fila','consultando','gerando','salvando','pronto','falhou','cancelado')),
            criado_em timestamptz NOT NULL DEFAULT now(),
            concluido_em timestamptz,
            visto boolean NOT NULL DEFAULT false,
            tentativa uuid,
            nome text,
            mime text,
            arquivo bytea,
            erro text
        );
        CREATE INDEX atlas_downloads_usuario ON atlas_downloads(cnpj, usuario, criado_em DESC);
        CREATE INDEX atlas_downloads_fila ON atlas_downloads(cnpj, criado_em) WHERE concluido_em IS NULL;
        CREATE INDEX atlas_downloads_limpeza ON atlas_downloads(cnpj, concluido_em) WHERE concluido_em IS NOT NULL;
        """);

    protected override void Down(MigrationBuilder migrationBuilder) =>
        migrationBuilder.Sql("DROP TABLE atlas_downloads");
}
