using System.Text;
using Atlas.Infrastructure;
using Npgsql;

internal static class EmissionChecks
{
    public static async Task RunAsync(string? connection)
    {
        const string cnpj = "00000000000001";
        const string key = "33261100000000000001550010000973011000000000";
        byte[] xml = Encoding.UTF8.GetBytes($"""
            <NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe Id="NFe{key}">
            <ide><nNF>97301</nNF><serie>1</serie><dhEmi>2026-11-20T09:33:15-03:00</dhEmi></ide>
            <emit><CNPJ>{cnpj}</CNPJ><xNome>Emitente de teste</xNome></emit>
            <dest><CNPJ>00000000000002</CNPJ><xNome>Destino de teste</xNome><enderDest><UF>RJ</UF></enderDest></dest>
            <det nItem="1"><prod><cProd>001</cProd><xProd>Peça A</xProd><NCM>87089990</NCM><CFOP>5101</CFOP>
            <uCom>UN</uCom><qCom>2.5000</qCom><vUnCom>10.00</vUnCom><vProd>25.00</vProd></prod>
            <imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST><vBC>25</vBC><pICMS>20</pICMS><vICMS>5</vICMS></ICMS00></ICMS></imposto></det>
            <det nItem="2"><prod><cProd>002</cProd><xProd>Peça B</xProd><NCM>73089090</NCM><CFOP>5101</CFOP>
            <uCom>UN</uCom><qCom>1</qCom><vUnCom>15</vUnCom><vProd>15</vProd></prod></det>
            <total><ICMSTot><vNF>40</vNF></ICMSTot><retTrib><vRetPIS>0.26</vRetPIS><vRetCOFINS>1.20</vRetCOFINS></retTrib></total>
            <transp><vol><pesoL>1.25</pesoL><pesoB>1.5</pesoB></vol><vol><pesoL>2.25</pesoL><pesoB>3</pesoB></vol></transp>
            </infNFe></NFe>
            """);
        var parsed = EmissionStore.Parse(cnpj, xml);
        var item = parsed.Itens[0];
        Check(item.GetProperty("Peso Liquido").GetDecimal() == 3.5m, "peso total de todos os volumes");
        Check(item.GetProperty("Peso Bruto").GetDecimal() == 4.5m, "peso bruto total");
        Check(item.GetProperty("Hora Emissao").GetString() == "09:33:15", "hora local sem conversão de fuso");
        Check(item.GetProperty("CST ICMS").GetString() == "000", "CST com origem do legado");
        Check(Equals(EmissionReports.Cell("Vl. Total", "1.234,56"), 1234.56m), "valores migrados pt-BR");
        Check(Equals(EmissionReports.Cell("Codigo", "00001"), "00001"), "códigos com zeros preservados");
        if (connection is null) return;
        Check(new NpgsqlConnectionStringBuilder(connection).Database?.StartsWith("atlas_test_", StringComparison.Ordinal) == true,
            "banco exclusivo para teste");
        // Usa CNPJ próprio para não se misturar aos cenários de protocolo da suíte.
        const string owner = "00000000000001";
        await using var db = new NpgsqlConnection(connection);
        await db.OpenAsync();
        await using (var delete = new NpgsqlCommand("DELETE FROM emissao_notas WHERE cnpj=$1 AND chave=$2", db))
        {
            delete.Parameters.AddWithValue(owner);
            delete.Parameters.AddWithValue(key);
            await delete.ExecuteNonQueryAsync();
        }
        try
        {
            var store = new EmissionStore(connection);
            using var stream = new MemoryStream(xml);
            Check((await store.ImportAsync(owner, [("teste.xml", stream)], default)).Imported == 1, "importação real");
            await using (var old = new NpgsqlCommand("""
                UPDATE emissao_notas SET itens=(SELECT jsonb_agg(item-'Peso Liquido'-'Peso Bruto'-'Hora Emissao')
                    FROM jsonb_array_elements(itens) item) WHERE cnpj=$1 AND chave=$2
                """, db))
            {
                old.Parameters.AddWithValue(owner);
                old.Parameters.AddWithValue(key);
                await old.ExecuteNonQueryAsync();
            }
            using var again = new MemoryStream(xml);
            Check((await store.ImportAsync(owner, [("teste.xml", again)], default)).Existing == 1,
                "reimportação enriquece metadados sem duplicação");
            var reports = new EmissionReports(connection);
            var book = await reports.WorkbookAsync(owner, "97301", new(2026, 11, 20), new(2026, 11, 20), "autorizada", "001", default);
            Check(book[0].Linhas.Count == 1 && book[1].Linhas.Count == 1 && book[2].Linhas.Count == 1, "mesmos filtros nas três abas");
            Check(Equals(book[3].Linhas[0]["Peso líq."], 3.5m), "resumo conserva o peso da nota, sem rateio por item");
            Check(Equals(book[2].Linhas[0]["Vl. COFINS Retido"], 1.20m), "retenção no nível da nota");
            Check((await reports.ReadAsync(owner, "itens", "Peça A", null, null, "", "002", "", 0, 101, default)).Linhas.Count == 0,
                "busca e filtro de item devem coincidir na mesma linha");
            Check((await reports.ReadAsync("00000000000002", "itens", "97301", null, null, "", "", "", 0, 101, default)).Linhas.Count == 0,
                "isolamento por CNPJ");
            using var changed = new MemoryStream(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(xml).Replace("Peça A", "Alterada")));
            Check((await store.ImportAsync(owner, [("teste.xml", changed)], default)).Existing == 1, "XML diferente não substitui a nota");
            var unchanged = await reports.ReadAsync(owner, "itens", "Alterada", null, null, "", "", key, 0, 101, default);
            Check(unchanged.Linhas.Count == 0, "conteúdo original preservado");
            Console.WriteLine("Emissão: XML, pesos, filtros combinados, retenções, resumo e isolamento por CNPJ conferidos.");
        }
        finally
        {
            await using var delete = new NpgsqlCommand("DELETE FROM emissao_notas WHERE cnpj=$1 AND chave=$2", db);
            delete.Parameters.AddWithValue(owner);
            delete.Parameters.AddWithValue(key);
            await delete.ExecuteNonQueryAsync();
        }
    }

    private static void Check(bool condition, string scenario)
    {
        if (!condition) throw new Exception($"Emissão: falha em {scenario}.");
    }
}
