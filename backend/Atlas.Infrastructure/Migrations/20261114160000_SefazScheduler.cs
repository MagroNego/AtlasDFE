using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

[DbContext(typeof(AtlasDbContext))]
[Migration("20261114160000_SefazScheduler")]
public sealed class SefazSchedulerMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE sefaz_fila (
            cnpj varchar(14) NOT NULL,
            nsu bigint NOT NULL CHECK (nsu BETWEEN 1 AND 999999999999999),
            alta boolean NOT NULL,
            status text NOT NULL CHECK (status IN ('pendente','recebido','indisponivel','cancelado')),
            criada_em timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(cnpj, nsu)
        );
        CREATE TABLE sefaz_agenda (
            cnpj varchar(14) PRIMARY KEY,
            ultima_reserva timestamptz NOT NULL
        );
        ALTER TABLE sefaz_tentativas ADD COLUMN nsu bigint;
        CREATE INDEX ON sefaz_tentativas(cnpj, nsu, tentado_em) WHERE recuperacao;
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        ALTER TABLE sefaz_tentativas DROP COLUMN nsu;
        DROP TABLE sefaz_agenda;
        DROP TABLE sefaz_fila;
        """);
}
