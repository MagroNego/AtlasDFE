using Microsoft.EntityFrameworkCore.Migrations;

namespace Atlas.Infrastructure.Migrations;

public sealed partial class ManualStatusMigration : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        CREATE TABLE atlas_situacao_manual (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cnpj varchar(14) NOT NULL, chave varchar(44) NOT NULL,
            usuario text NOT NULL, motivo text NOT NULL,
            cancelada boolean NOT NULL, alterado_em timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON atlas_situacao_manual(cnpj,chave,id DESC);
        CREATE VIEW atlas_situacao_atual AS
            SELECT DISTINCT ON (cnpj,chave) cnpj,chave,cancelada
            FROM atlas_situacao_manual ORDER BY cnpj,chave,id DESC;
        """);

    protected override void Down(MigrationBuilder migrationBuilder) => migrationBuilder.Sql("""
        DROP VIEW atlas_situacao_atual;
        DROP TABLE atlas_situacao_manual;
        """);
}
