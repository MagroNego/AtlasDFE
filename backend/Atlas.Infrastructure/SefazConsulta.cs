using System.Globalization;
using System.Text.Json;
using System.Xml.Linq;
using Atlas.Domain;
using Npgsql;
using NpgsqlTypes;

namespace Atlas.Infrastructure;

internal static class SefazConsulta
{
    private static readonly XNamespace Nfe = "http://www.portalfiscal.inf.br/nfe";

    public static async Task PublishAsync(NpgsqlConnection db, NpgsqlTransaction tx, string cnpj,
        SefazDocument document, CancellationToken token, bool imported = false)
    {
        var root = SefazDistribution.ReadXml(document.Xml, 8 * 1024 * 1024);
        if (root.Name.Namespace != Nfe)
            return;

        var request = root.Name == Nfe + "procEventoNFe" ? root.Element(Nfe + "evento")?.Element(Nfe + "infEvento") : root;
        var receipt = root.Element(Nfe + "retEvento")?.Element(Nfe + "infEvento");
        var note = root.Element(Nfe + "NFe")?.Element(Nfe + "infNFe");
        string key = ((string?)note?.Attribute("Id")) is { } id && id.StartsWith("NFe", StringComparison.Ordinal)
            ? id[3..] : Value(request, "chNFe");
        if (IdentificadorNota.Parse(key)?.Chave != key || key.Length != 44)
        {
            if (imported)
                throw new InvalidDataException("Chave de NF-e inválida no evento.");
            return;
        }

        if (imported && key[6..20] != cnpj)
            throw new InvalidDataException("O evento pertence a outro emitente.");
        string code = Value(request, "tpEvento");
        string description = code switch
        {
            "210200" => "Confirmação da Operação",
            "210210" => "Ciência da Operação",
            "210220" => "Desconhecimento da Operação",
            "210240" => "Operação não Realizada",
            "110110" => "Carta de Correção",
            _ => "",
        };
        bool fullEvent = root.Name == Nfe + "procEventoNFe";
        bool linked = !fullEvent || (Value(receipt, "chNFe") == key &&
            Value(receipt, "tpEvento") == code && Value(receipt, "tpAmb") == "1" &&
            Value(request, "tpAmb") == "1" && Value(receipt, "nSeqEvento") == Value(request, "nSeqEvento"));
        string status = Value(receipt, "cStat");
        bool canceled = root.Name == Nfe + "resNFe" && Value(root, "cSitNFe") == "3"
            || root.Name == Nfe + "resEvento" && code == "110111"
            || fullEvent && linked && code == "110111" && status is ("135" or "155");
        string protocol = First(Value(receipt, "nProt"), Value(request, "nProt"));
        string correction = Value(request?.Element(Nfe + "detEvento"), "xCorrecao");
        if (imported && (code is not ("110110" or "110111") || !linked ||
            Value(request, "CNPJ") != cnpj || protocol.Length != 15 || !protocol.All(char.IsAsciiDigit) ||
            (code == "110110" && (status != "135" || correction.Length is < 15 or > 1000)) ||
            (code == "110111" && !canceled)))
            throw new InvalidDataException("Envie um cancelamento ou uma CC-e vinculada pela SEFAZ.");
        bool acceptedEvent = linked && description.Length > 0 &&
            (root.Name == Nfe + "resEvento" || fullEvent && status is ("135" or "136" or "155"));
        string transportKey = Value(request, "chCTe");
        bool transportEvent = code is ("610600" or "610601") && linked
            && (root.Name == Nfe + "resEvento" || fullEvent && status is ("135" or "136" or "155"));
        if (transportKey.Length > 0 && (transportKey.Length != 44 || !transportKey.All(char.IsAsciiDigit)
            || transportKey[20..22] != "57"))
            transportEvent = false;

        if (!acceptedEvent && !transportEvent && !canceled && root.Name != Nfe + "resNFe" && note is null)
            return;

        var events = new List<object>();
        if (acceptedEvent)
            events.Add(new
            {
                codigo = code, descricao = description,
                data = First(Value(receipt, "dhRegEvento"), Value(request, "dhEvento")),
                protocolo = protocol,
                nsu = imported ? "" : document.Nsu.ToString("D15", CultureInfo.InvariantCulture),
                schema = document.Schema,
                correcao = code == "110110" ? correction : "",
                sequencia = Value(request, "nSeqEvento"),
            });

        var links = new List<object>();
        if (transportEvent)
        {
            var carrier = request?.Descendants(Nfe + "emit").FirstOrDefault();
            string transportProtocol = First(Value(receipt, "nProt"), Value(root, "nProt"));
            string sequence = Value(request, "nSeqEvento");
            string date = First(Value(request, "dhEvento"), Value(root, "dhRecbto"));
            links.Add(new
            {
                chave = transportKey,
                status = code == "610601" ? "Cancelado" : transportKey.Length > 0 ? "Autorizado" : "Vínculo identificado",
                transportadora = Value(carrier, "xNome"), cnpj_transportadora = Value(carrier, "CNPJ"), data = date,
                identidade = $"{code}:{First(sequence, First(transportProtocol, date))}",
            });
        }

        await using var command = new NpgsqlCommand("""
            INSERT INTO consulta_notas(cnpj,chave,numero,serie,detalhe)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (cnpj,chave) DO UPDATE SET detalhe = consulta_notas.detalhe ||
                jsonb_build_object('cancelada', (consulta_notas.detalhe->>'cancelada')::boolean
                    OR (excluded.detalhe->>'cancelada')::boolean,
                    'eventos', CASE WHEN jsonb_array_length(excluded.detalhe->'eventos') = 0
                        OR EXISTS (SELECT 1 FROM jsonb_array_elements(consulta_notas.detalhe->'eventos') e
                            WHERE e->>'codigo'=excluded.detalhe->'eventos'->0->>'codigo'
                              AND (e->>'protocolo'=excluded.detalhe->'eventos'->0->>'protocolo'
                                  OR (e->>'nsu' <> '' AND e->>'nsu'=excluded.detalhe->'eventos'->0->>'nsu')))
                        THEN consulta_notas.detalhe->'eventos'
                        ELSE (consulta_notas.detalhe->'eventos') || (excluded.detalhe->'eventos') END,
                    'ctes', CASE WHEN jsonb_array_length(excluded.detalhe->'ctes') = 0
                        THEN consulta_notas.detalhe->'ctes'
                        ELSE (SELECT coalesce(jsonb_agg(link), '[]'::jsonb) FROM (
                            SELECT DISTINCT ON (CASE WHEN link->>'chave' <> '' THEN link->>'chave'
                                ELSE coalesce(link->>'identidade',link::text) END) link
                            FROM jsonb_array_elements((consulta_notas.detalhe->'ctes') || (excluded.detalhe->'ctes'))
                                WITH ORDINALITY AS items(link, position)
                            ORDER BY CASE WHEN link->>'chave' <> '' THEN link->>'chave'
                                ELSE coalesce(link->>'identidade',link::text) END,
                                (link->>'status'='Cancelado') DESC, position DESC
                        ) AS latest) END)
            """, db, tx);
        command.Parameters.AddWithValue(cnpj);
        command.Parameters.AddWithValue(key);
        command.Parameters.AddWithValue(int.Parse(key.AsSpan(25, 9), CultureInfo.InvariantCulture));
        command.Parameters.AddWithValue(int.Parse(key.AsSpan(22, 3), CultureInfo.InvariantCulture));
        command.Parameters.AddWithValue(NpgsqlDbType.Jsonb,
            JsonSerializer.Serialize(new { cancelada = canceled, eventos = events, ctes = links }));
        await command.ExecuteNonQueryAsync(token);

        static string First(string value, string fallback) => value.Length > 0 ? value : fallback;
    }

    private static string Value(XElement? node, string name) => node?.Element(Nfe + name)?.Value.Trim() ?? "";
}
