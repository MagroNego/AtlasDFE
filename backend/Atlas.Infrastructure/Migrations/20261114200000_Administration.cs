using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114200000_Administration")]
public sealed class AdministrationMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE atlas_auditoria (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cnpj varchar(14) NOT NULL, usuario text NOT NULL, acao text NOT NULL,
            alvo text NOT NULL, criado_em timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON atlas_auditoria(cnpj,id DESC);
        CREATE TABLE atlas_alertas (
            cnpj varchar(14) NOT NULL, nsu bigint NOT NULL, chave varchar(44) NOT NULL,
            criado_em timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(cnpj,nsu)
        );
        CREATE TABLE atlas_alertas_entregas (
            cnpj varchar(14) NOT NULL, nsu bigint NOT NULL, email text NOT NULL,
            tentativas integer NOT NULL DEFAULT 0, tentar_em timestamptz NOT NULL DEFAULT now(),
            enviado_em timestamptz,
            PRIMARY KEY(cnpj,nsu,email),
            FOREIGN KEY(cnpj,nsu) REFERENCES atlas_alertas(cnpj,nsu)
        );
        CREATE INDEX ON atlas_alertas_entregas(cnpj,tentar_em) WHERE enviado_em IS NULL;
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        DROP TABLE atlas_alertas_entregas;
        DROP TABLE atlas_alertas;
        DROP TABLE atlas_auditoria;
        """);
}
