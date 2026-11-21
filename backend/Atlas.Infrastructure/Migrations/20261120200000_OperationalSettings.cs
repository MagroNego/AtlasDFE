using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261120200000_OperationalSettings")]
public sealed class OperationalSettingsMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE atlas_operacao (
            cnpj varchar(14) NOT NULL,
            tipo text NOT NULL CHECK (tipo IN ('agenda','alertas','certificado')),
            dados jsonb NOT NULL,
            segredo bytea,
            CHECK ((tipo='certificado') = (segredo IS NOT NULL)),
            PRIMARY KEY(cnpj,tipo)
        );
        """);

    protected override void Down(MigrationBuilder migrationBuilder) =>
        migrationBuilder.Sql("DROP TABLE atlas_operacao");
}
