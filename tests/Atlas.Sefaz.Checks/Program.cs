using System.IO.Compression;
using System.Text;
using Atlas.Infrastructure;
using Npgsql;

const string cnpj = "12345678000199";
const long cursor = 41;
byte[] doc = Encoding.UTF8.GetBytes("<resNFe xmlns=\"http://www.portalfiscal.inf.br/nfe\" />");
using var zipped = new MemoryStream();
using (var gzip = new GZipStream(zipped, CompressionMode.Compress, leaveOpen: true))
    gzip.Write(doc);
string zip = Convert.ToBase64String(zipped.ToArray());

string Response(int status, string fields = "") =>
    $"<retDistDFeInt xmlns=\"http://www.portalfiscal.inf.br/nfe\"><cStat>{status}</cStat>{fields}</retDistDFeInt>";
string Document(long nsu) => $"<docZip NSU=\"{nsu}\" schema=\"resNFe_v1.01.xsd\">{zip}</docZip>";

var request = SefazDistribution.Request(cnpj, "33", cursor, recovery: false);
Check(request.Contains("<ultNSU>000000000000041</ultNSU>"));
Check(SefazDistribution.Request(cnpj, "33", 42, recovery: true).Contains("<NSU>000000000000042</NSU>"));

var lote = SefazDistribution.Parse(Response(138, $"<ultNSU>42</ultNSU><maxNSU>43</maxNSU><loteDistDFeInt>{Document(42)}</loteDistDFeInt>"));
Check(lote.CursorAfter(cursor) == 42);
Check(lote.Documents.Single().Xml.SequenceEqual(doc));
Check(lote.CursorAfter(50, recovery: true, requested: 42) == 50);
Invalid(() => lote.CursorAfter(50, recovery: true, requested: 43));
Invalid(() => lote.CursorAfter(42));
Invalid(() => SefazDistribution.Parse(Response(138, $"<ultNSU>42</ultNSU><maxNSU>42</maxNSU><loteDistDFeInt>{Document(42)}{Document(42)}</loteDistDFeInt>")));
Invalid(() => SefazDistribution.Parse("<!DOCTYPE root [<!ENTITY test SYSTEM 'file:///etc/passwd'>]>" + Response(137)));
Invalid(() => SefazDistribution.Parse(Response(137, "<ultNSU>42</ultNSU><maxNSU>43</maxNSU>")).CursorAfter(cursor));

var noDocs = SefazDistribution.Parse(Response(137, "<ultNSU>41</ultNSU><maxNSU>41</maxNSU>"));
Check(noDocs.CursorAfter(cursor) == cursor);
Check(noDocs.RetryAt(DateTimeOffset.UnixEpoch) == DateTimeOffset.UnixEpoch.AddHours(1));
var blocked = SefazDistribution.Parse(Response(656, "<ultNSU>77</ultNSU>"));
Check(blocked.LastNsu == 77);
Check(blocked.CursorAfter(cursor) == cursor);
Check(blocked.RetryAt(DateTimeOffset.UnixEpoch) == DateTimeOffset.UnixEpoch.AddHours(1));

Check(!SefazDistribution.InWindow(DateTimeOffset.Parse("2026-11-14T00:59:59Z"), new(22,0), new(4,0)));
Check(SefazDistribution.InWindow(DateTimeOffset.Parse("2026-11-14T01:00:00Z"), new(22,0), new(4,0)));
Check(!SefazDistribution.InWindow(DateTimeOffset.Parse("2026-11-14T07:00:00Z"), new(22,0), new(4,0)));
Console.WriteLine("Verificações do protocolo e do cursor concluídas.");

if (Environment.GetEnvironmentVariable("ATLAS_TEST_DATABASE") is { } connection)
{
    Check(new NpgsqlConnectionStringBuilder(connection).Database?.StartsWith("atlas_test_", StringComparison.Ordinal) == true);
    var inbox = new SefazInbox(connection);
    string nsu42 = Response(138, $"<ultNSU>42</ultNSU><maxNSU>43</maxNSU><loteDistDFeInt>{Document(42)}</loteDistDFeInt>");
    var first = await Replay(cursor, false, nsu42);
    Check(first.Cursor == 42 && first.Documents == 1 && !first.AlreadyApplied);
    Check((await Replay(cursor, false, nsu42)).AlreadyApplied);
    string recovery = Response(138, $"<ultNSU>0</ultNSU><maxNSU>0</maxNSU><loteDistDFeInt>{Document(9)}</loteDistDFeInt>");
    Check((await Replay(9, true, recovery)).Cursor == 42);
    try
    {
        await Replay(42, false, "<incompleto");
        throw new Exception("Resposta inválida foi aplicada.");
    }
    catch (InvalidDataException) { }

    const string nsuSefaz = "<ultNSU>77</ultNSU>";
    Check((await Replay(42, false, Response(656, nsuSefaz))).Cursor == 42);
    await using var db = new NpgsqlConnection(connection);
    await db.OpenAsync();
    await using var verify = new NpgsqlCommand("""
        SELECT ult_nsu, max_nsu, proxima_tentativa IS NOT NULL,
            (SELECT count(*) FROM sefaz_respostas WHERE cstat IS NULL AND cnpj=$1),
            (SELECT count(*) FROM sefaz_documentos WHERE cnpj=$1)
        FROM sefaz_estado WHERE cnpj=$1
        """, db);
    verify.Parameters.AddWithValue(cnpj);
    await using var rows = await verify.ExecuteReaderAsync();
    Check(await rows.ReadAsync());
    Check(rows.GetInt64(0) == 42 && rows.GetInt64(1) == 43 && rows.GetBoolean(2)
        && rows.GetInt64(3) == 1 && rows.GetInt64(4) == 2);
    Console.WriteLine("PostgreSQL: reprocessamento sem duplicação, resposta inválida preservada e cursor íntegro.");

    Task<SefazReplayResult> Replay(long sent, bool recovery, string xml) =>
        inbox.ReplayAsync(cnpj, sent, recovery, Encoding.UTF8.GetBytes(xml), CancellationToken.None);
}

static void Check(bool condition)
{
    if (!condition) throw new Exception("Resultado inesperado.");
}

static void Invalid(Action action)
{
    try { action(); }
    catch (InvalidDataException) { return; }
    throw new Exception("O protocolo deveria rejeitar a entrada.");
}
