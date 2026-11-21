using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261120220000_ParityHistory")]
public sealed class ParityHistoryMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE atlas_importacao_xml (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, cnpj varchar(14) NOT NULL,
          usuario text NOT NULL, criado_em timestamptz NOT NULL DEFAULT now(), resultado jsonb NOT NULL
        );
        CREATE INDEX ON atlas_importacao_xml(cnpj,id DESC);
        CREATE TABLE atlas_integridade_migracao (
          cnpj varchar(14) PRIMARY KEY, verificado_em timestamptz NOT NULL DEFAULT now(),
          publicado boolean NOT NULL, medidas jsonb NOT NULL
        );
        """);

    protected override void Down(MigrationBuilder migrationBuilder) =>
        migrationBuilder.Sql("DROP TABLE atlas_importacao_xml, atlas_integridade_migracao");
}
