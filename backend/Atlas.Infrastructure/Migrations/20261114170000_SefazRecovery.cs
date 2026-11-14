using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114170000_SefazRecovery")]
public sealed class SefazRecoveryMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        ALTER TABLE sefaz_tentativas ADD COLUMN legada boolean NOT NULL DEFAULT false;
        CREATE TABLE sefaz_lacunas (
            cnpj varchar(14) NOT NULL,
            inicio bigint NOT NULL,
            fim bigint NOT NULL CHECK (fim > inicio),
            PRIMARY KEY(cnpj,inicio,fim)
        );
        CREATE TABLE sefaz_cobertura (
            cnpj varchar(14) NOT NULL,
            nsu bigint NOT NULL,
            PRIMARY KEY(cnpj,nsu)
        );
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        ALTER TABLE sefaz_tentativas DROP COLUMN legada;
        DROP TABLE sefaz_cobertura;
        DROP TABLE sefaz_lacunas;
        """);
}
