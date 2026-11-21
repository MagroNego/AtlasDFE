using Atlas.Infrastructure;
using Microsoft.Data.Sqlite;
using Npgsql;

internal static class LegacyMigrationChecks
{
    public static async Task RunAsync(string? connection)
    {
        if (connection is null) return;
        if (new NpgsqlConnectionStringBuilder(connection).Database?.StartsWith("atlas_test_", StringComparison.Ordinal) != true)
            throw new InvalidOperationException("Use um banco de teste para conferir a migração.");
        const string cnpj = "00000000000191";
        const string key = "332611" + cnpj + "550010000974011000000000";
        string file = Path.Combine(Path.GetTempPath(), $"atlas-{Guid.NewGuid()}.sqlite");
        SQLitePCL.Batteries_V2.Init();
        try
        {
            using (var source = new SqliteConnection(new SqliteConnectionStringBuilder
            {
                DataSource = file, Pooling = false,
            }.ToString()))
            {
                source.Open();
                using var seed = source.CreateCommand();
                seed.CommandText = """
                    CREATE TABLE manifestacoes(id integer primary key, cnpj text, chave text, codigo text,
                        descricao text, data_evento text, protocolo text, nsu text, schema_xml text);
                    INSERT INTO manifestacoes VALUES (1,$cnpj,$key,'210200','Confirmação','2026-11-20','123','9','resEvento');
                    """;
                seed.Parameters.AddWithValue("$cnpj", cnpj);
                seed.Parameters.AddWithValue("$key", key);
                seed.ExecuteNonQuery();
            }

            await using var db = new NpgsqlConnection(connection);
            await db.OpenAsync();
            await Command("DELETE FROM consulta_notas WHERE cnpj=$1", cnpj);
            if (await LegacyHistoryImport.ImportAsync(connection, file, cnpj, null, default) != 1)
                throw new Exception("A cópia de teste deveria publicar uma nota.");
            if ((bool?)await Query("SELECT publicado FROM atlas_integridade_migracao WHERE cnpj=$1", cnpj) != true)
                throw new Exception("A conferência deveria aprovar a cópia.");

            await Command("""
                CREATE FUNCTION atlas_qa_legacy_mismatch() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.cnpj='00000000000191' THEN
                        NEW.detalhe=jsonb_set(NEW.detalhe,'{eventos}','[]'::jsonb);
                    END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER atlas_qa_legacy_mismatch BEFORE INSERT ON consulta_notas
                    FOR EACH ROW EXECUTE FUNCTION atlas_qa_legacy_mismatch()
                """);
            try
            {
                try
                {
                    await LegacyHistoryImport.ImportAsync(connection, file, cnpj, null, default);
                    throw new Exception("A conferência deveria bloquear a publicação divergente.");
                }
                catch (InvalidDataException error) when (error.Message.Contains("encontrou diferenças", StringComparison.Ordinal)) { }
                if ((bool?)await Query("SELECT publicado FROM atlas_integridade_migracao WHERE cnpj=$1", cnpj) != false
                    || (int?)await Query("SELECT jsonb_array_length(detalhe->'eventos') FROM consulta_notas WHERE cnpj=$1", cnpj) != 1)
                    throw new Exception("A divergência deve ser registrada e a nota anterior preservada.");
            }
            finally
            {
                await Command("DROP TRIGGER atlas_qa_legacy_mismatch ON consulta_notas; DROP FUNCTION atlas_qa_legacy_mismatch()");
            }
            Console.WriteLine("Migração SQLite → PostgreSQL: publicação e reversão por divergência conferidas.");

            async Task Command(string sql, string? company = null)
            {
                await using var command = new NpgsqlCommand(sql, db);
                if (company is not null) command.Parameters.AddWithValue(company);
                await command.ExecuteNonQueryAsync();
            }
            async Task<object?> Query(string sql, string company)
            {
                await using var command = new NpgsqlCommand(sql, db);
                command.Parameters.AddWithValue(company);
                return await command.ExecuteScalarAsync();
            }
        }
        finally { File.Delete(file); }
    }
}
