using System.Text.Json;
using Microsoft.Data.Sqlite;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

public static class LegacyHistoryImport
{
    public static async Task<int> ImportAsync(
        string connectionString, string origin, string cnpj, string? password,
        CancellationToken cancellationToken)
    {
        SQLitePCL.Batteries_V2.Init();
        using var source = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = Path.GetFullPath(origin),
            Mode = SqliteOpenMode.ReadOnly,
            Pooling = false,
            Password = password ?? "",
        }.ToString());
        source.Open();

        await using var destination = new NpgsqlConnection(connectionString);
        await destination.OpenAsync(cancellationToken);
        await using var write = await destination.BeginTransactionAsync(cancellationToken);

        await using var prepare = new NpgsqlCommand("""
            SELECT pg_advisory_xact_lock(718322, hashtext(@cnpj));
            SELECT pg_advisory_xact_lock(718320, hashtext(@cnpj));
            DELETE FROM consulta_notas WHERE cnpj=@cnpj
            AND NOT EXISTS (SELECT 1 FROM sefaz_tentativas WHERE cnpj=@cnpj AND NOT legada)
            AND NOT EXISTS (SELECT 1 FROM sefaz_respostas WHERE cnpj=@cnpj AND cstat IS NOT NULL);
            """, destination, write);
        prepare.Parameters.AddWithValue("cnpj", cnpj);
        await prepare.ExecuteNonQueryAsync(cancellationToken);
        await using (var active = new NpgsqlCommand(
            """
            SELECT EXISTS (SELECT 1 FROM sefaz_tentativas WHERE cnpj=@cnpj AND NOT legada)
                OR EXISTS (SELECT 1 FROM sefaz_respostas WHERE cnpj=@cnpj AND cstat IS NOT NULL)
            """, destination, write))
        {
            active.Parameters.AddWithValue("cnpj", cnpj);
            if ((bool?)await active.ExecuteScalarAsync(cancellationToken) == true)
                throw new InvalidDataException("Importe o histórico antes de ativar a sincronização C# deste CNPJ.");
        }

        using var read = source.BeginTransaction(deferred: true);
        var history = new LegacyHistory(read, cnpj);

        int count = 0;
        string copySql = "COPY consulta_notas (cnpj,chave,numero,serie,detalhe) FROM STDIN (FORMAT BINARY)";
        await using (var copy = await destination.BeginBinaryImportAsync(copySql, cancellationToken))
        {
            foreach (var key in history.Keys())
            {
                if (Atlas.Domain.IdentificadorNota.Parse(key)?.Chave != key)
                    throw new InvalidDataException("A origem contém uma chave de NF-e inválida.");

                string details = history.Details(key);
                await copy.StartRowAsync(cancellationToken);
                await copy.WriteAsync(cnpj, NpgsqlDbType.Varchar, cancellationToken);
                await copy.WriteAsync(key, NpgsqlDbType.Varchar, cancellationToken);
                await copy.WriteAsync(int.Parse(key.AsSpan(25, 9)), NpgsqlDbType.Integer, cancellationToken);
                await copy.WriteAsync(int.Parse(key.AsSpan(22, 3)), NpgsqlDbType.Integer, cancellationToken);
                await copy.WriteAsync(details, NpgsqlDbType.Jsonb, cancellationToken);
                count++;
            }
            await copy.CompleteAsync(cancellationToken);
        }

        await using var finish = new NpgsqlCommand("""
            INSERT INTO consulta_importacoes (cnpj,importado_em,total)
            VALUES (@cnpj,now(),@total)
            ON CONFLICT (cnpj) DO UPDATE SET importado_em=excluded.importado_em,total=excluded.total
            WHERE excluded.total > 0
            """, destination, write);
        finish.Parameters.AddWithValue("cnpj", cnpj);
        finish.Parameters.AddWithValue("total", count);
        if (await finish.ExecuteNonQueryAsync(cancellationToken) == 0)
            throw new InvalidDataException("A origem não tem notas deste CNPJ. A cópia anterior foi preservada.");
        await LegacyEmissionImport.RunAsync(read, write, cnpj, cancellationToken);
        await new LegacySefazImport(read, write, cnpj).RunAsync(cancellationToken);
        await write.CommitAsync(cancellationToken);
        return count;
    }

    private sealed class LegacyHistory(SqliteTransaction transaction, string cnpj)
    {
        private readonly HashSet<string> tables = ReadTables(transaction);

        private static HashSet<string> ReadTables(SqliteTransaction tx)
        {
            using var command = tx.Connection!.CreateCommand();
            command.Transaction = tx;
            command.CommandText = "SELECT name FROM sqlite_master WHERE type='table'";
            using var rows = command.ExecuteReader();
            var found = new HashSet<string>(StringComparer.Ordinal);
            while (rows.Read())
                found.Add(rows.GetString(0));
            if (!found.Contains("manifestacoes"))
                throw new InvalidDataException("A origem não é um banco fiscal do Atlas.");
            return found;
        }

        private SqliteCommand Query(string sql, string? key = null)
        {
            var command = transaction.Connection!.CreateCommand();
            command.Transaction = transaction;
            command.CommandText = sql;
            command.Parameters.AddWithValue("$cnpj", cnpj);
            if (key is not null)
                command.Parameters.AddWithValue("$chave", key);
            return command;
        }

        public IEnumerable<string> Keys()
        {
            (string Table, string Column)[] sources =
            [
                ("manifestacoes", "chave"), ("informacoes_nfe", "chave"),
                ("xml_documentos", "chave"), ("cancelamentos_manuais", "chave"),
                ("eventos_cte", "chave_nfe"),
            ];
            var selects = sources.Where(s => tables.Contains(s.Table))
                .Select(s => $"SELECT {s.Column} FROM {s.Table} WHERE cnpj=$cnpj");
            using var command = Query(string.Join(" UNION ", selects));
            using var rows = command.ExecuteReader();
            while (rows.Read())
                yield return rows.IsDBNull(0) ? "" : rows.GetString(0);
        }

        public string Details(string key) => JsonSerializer.Serialize(new
        {
            cancelada = Cancelled(key),
            eventos = Events(key),
            ctes = Links(key),
        });

        private bool Cancelled(string key)
        {
            return Exists("informacoes_nfe", "AND cancelada=1") || Exists("cancelamentos_manuais", "");

            bool Exists(string table, string condition)
            {
                if (!tables.Contains(table))
                    return false;
                using var command = Query($"SELECT 1 FROM {table} WHERE cnpj=$cnpj AND chave=$chave {condition} LIMIT 1", key);
                return command.ExecuteScalar() is not null;
            }
        }

        private List<object> Events(string key)
        {
            string join = tables.Contains("cartas_correcao")
                ? "LEFT JOIN cartas_correcao c ON c.cnpj=m.cnpj AND c.chave=m.chave AND c.protocolo=m.protocolo AND m.codigo='110110'"
                : "";
            string correction = join.Length == 0 ? "'',''" : "COALESCE(c.correcao,''), COALESCE(c.sequencia,'')";
            using var command = Query($"""
                SELECT m.codigo,m.descricao,m.data_evento,m.protocolo,m.nsu,m.schema_xml,{correction}
                FROM manifestacoes m {join}
                WHERE m.cnpj=$cnpj AND m.chave=$chave ORDER BY m.data_evento,CAST(m.nsu AS INTEGER),m.id
                """, key);
            using var rows = command.ExecuteReader();
            var events = new List<object>();
            while (rows.Read())
                events.Add(new
                {
                    codigo = rows.GetString(0), descricao = rows.GetString(1), data = rows.GetString(2),
                    protocolo = rows.GetString(3), nsu = rows.GetString(4), schema = rows.GetString(5),
                    correcao = rows.GetString(6), sequencia = rows.GetString(7),
                });
            return events;
        }

        private IEnumerable<CteLink> Links(string key)
        {
            if (!tables.Contains("eventos_cte"))
                return [];
            using var command = Query("""
                SELECT chave_cte,codigo,transportadora,cnpj_transportadora,data_evento,protocolo,sequencia
                FROM eventos_cte WHERE cnpj=$cnpj AND chave_nfe=$chave ORDER BY id
                """, key);
            using var rows = command.ExecuteReader();
            var events = new List<CteEvent>();
            var protocols = new Dictionary<(string, string), string>();
            var sequences = new Dictionary<(string, string), string>();
            while (rows.Read())
            {
                var evt = new CteEvent(rows.GetString(0), rows.GetString(1), rows.GetString(2),
                    rows.GetString(3), rows.GetString(4), rows.GetString(5), rows.GetString(6));
                events.Add(evt);
                if (evt.Key.Length > 0 && evt.Protocol.Length > 0)
                    protocols[(evt.Code, evt.Protocol)] = evt.Key;
                if (evt.Key.Length > 0 && evt.Sequence.Length > 0)
                    sequences[(evt.Code, evt.Sequence)] = evt.Key;
            }

            var links = new Dictionary<string, CteLink>();
            foreach (var evt in events)
            {
                var resolved = NonEmpty(evt.Key, protocols.GetValueOrDefault((evt.Code, evt.Protocol)))
                    ?? sequences.GetValueOrDefault((evt.Code, evt.Sequence));
                var identifier = resolved ?? $"{evt.Code}:{NonEmpty(evt.Sequence, NonEmpty(evt.Protocol, evt.Date))}";
                links.TryGetValue(identifier, out var prior);
                bool canceled = evt.Code == "610601";
                links[identifier] = new CteLink(
                    resolved ?? "",
                    canceled || prior?.status == "Cancelado" ? "Cancelado" : resolved is null ? "Vínculo identificado" : "Autorizado",
                    NonEmpty(evt.Carrier, prior?.transportadora) ?? "",
                    NonEmpty(evt.CarrierId, prior?.cnpj_transportadora) ?? "",
                    canceled || prior is null ? evt.Date : prior.data);
            }
            return links.Values;
        }

        private static string? NonEmpty(string value, string? fallback) => value.Length > 0 ? value : fallback;
        private sealed record CteEvent(string Key, string Code, string Carrier, string CarrierId,
            string Date, string Protocol, string Sequence);
        private sealed record CteLink(string chave, string status, string transportadora,
            string cnpj_transportadora, string data);
    }
}
