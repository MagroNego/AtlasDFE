using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114180000_Emission")]
public sealed class EmissionMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE emissao_notas (
          cnpj varchar(14) NOT NULL, chave varchar(44) NOT NULL, numero integer NOT NULL,
          serie integer NOT NULL, emissao date NOT NULL, emitente text NOT NULL,
          destinatario text NOT NULL, valor numeric NOT NULL, itens jsonb NOT NULL,
          pis_retido numeric NOT NULL DEFAULT 0, cofins_retido numeric NOT NULL DEFAULT 0,
          hash text NOT NULL, PRIMARY KEY(cnpj,chave)
        );
        CREATE INDEX ON emissao_notas(cnpj,emissao DESC,chave);
        """);

    protected override void Down(MigrationBuilder migrationBuilder) =>
        migrationBuilder.Sql("DROP TABLE emissao_notas;");
}
