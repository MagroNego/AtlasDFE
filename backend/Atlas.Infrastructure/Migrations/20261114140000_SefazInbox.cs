using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114140000_SefazInbox")]
public sealed class SefazInboxMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE sefaz_respostas (
            cnpj varchar(14) NOT NULL,
            nsu_enviado bigint NOT NULL,
            recuperacao boolean NOT NULL,
            hash bytea NOT NULL,
            xml bytea NOT NULL,
            recebido_em timestamptz NOT NULL DEFAULT now(),
            cstat integer,
            cursor_final bigint,
            documentos integer,
            PRIMARY KEY(cnpj, nsu_enviado, recuperacao, hash)
        );
        CREATE TABLE sefaz_estado (
            cnpj varchar(14) PRIMARY KEY,
            ult_nsu bigint NOT NULL,
            max_nsu bigint,
            proxima_tentativa timestamptz
        );
        CREATE TABLE sefaz_documentos (
            cnpj varchar(14) NOT NULL,
            nsu bigint NOT NULL,
            schema_xml varchar(120) NOT NULL,
            xml bytea NOT NULL,
            PRIMARY KEY(cnpj, nsu)
        );
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        DROP TABLE sefaz_documentos;
        DROP TABLE sefaz_estado;
        DROP TABLE sefaz_respostas;
        """);
}
