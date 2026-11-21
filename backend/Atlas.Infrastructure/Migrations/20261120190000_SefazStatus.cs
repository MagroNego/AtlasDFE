using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261120190000_SefazStatus")]
public sealed class SefazStatusMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        ALTER TABLE sefaz_respostas ADD COLUMN processado_em timestamptz;
        CREATE INDEX sefaz_status_respostas ON sefaz_respostas(cnpj, recebido_em DESC);
        CREATE INDEX sefaz_status_processadas ON sefaz_respostas(cnpj, processado_em DESC)
            WHERE processado_em IS NOT NULL;
        CREATE INDEX sefaz_status_pendentes ON sefaz_respostas(cnpj, recebido_em) WHERE cstat IS NULL;
        CREATE INDEX sefaz_status_tentativas ON sefaz_tentativas(cnpj, tentado_em DESC) WHERE NOT legada;
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        DROP INDEX sefaz_status_tentativas, sefaz_status_pendentes, sefaz_status_processadas, sefaz_status_respostas;
        ALTER TABLE sefaz_respostas DROP COLUMN processado_em;
        """);
}
