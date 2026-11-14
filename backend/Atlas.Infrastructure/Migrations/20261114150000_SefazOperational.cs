using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114150000_SefazOperational")]
public sealed class SefazOperationalMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE sefaz_tentativas (
            cnpj varchar(14) NOT NULL,
            recuperacao boolean NOT NULL,
            tentado_em timestamptz NOT NULL
        );
        CREATE INDEX ON sefaz_tentativas(cnpj, recuperacao, tentado_em);
        """);

    protected override void Down(MigrationBuilder migrationBuilder) =>
        migrationBuilder.Sql("DROP TABLE sefaz_tentativas;");
}
