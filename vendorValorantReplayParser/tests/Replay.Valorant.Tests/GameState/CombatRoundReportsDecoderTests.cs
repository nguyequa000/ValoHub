using Replay.Encoding.Archives;
using Replay.Unreal.Parsing;
using Replay.Valorant.GameState;

namespace Replay.Valorant.Tests.GameState;

public class CombatRoundReportsDecoderTests
{
    [TestCase("0000", 0)]
    [TestCase("0A00", 5)]
    public void Decode_PreservesDeclaredLengthWhenNoElementsChanged(string hex, int declaredCount)
    {
        using var archive = new BitArchiveReader(Convert.FromHexString(hex));
        var context = new FieldDecodeContext();
        var update = (CombatReportArrayUpdate<CombatRoundReportUpdate>)Decoder().Decode(ref context, archive).ObjectValue!;
        Assert.Multiple(() =>
        {
            Assert.That(update.DeclaredCount, Is.EqualTo(declaredCount));
            Assert.That(update.Updates, Is.Empty);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase("02020000", false)]
    [TestCase("02020A2000000000", true)]
    public void Decode_DistinguishesAbsentReportsFromDecodedEmptyArray(string hex, bool hasReports)
    {
        using var archive = new BitArchiveReader(Convert.FromHexString(hex));
        var context = new FieldDecodeContext();
        var update = (CombatReportArrayUpdate<CombatRoundReportUpdate>)Decoder().Decode(ref context, archive).ObjectValue!;
        var round = update.Updates.Single();
        Assert.That(round.Reports is not null, Is.EqualTo(hasReports));
        if (hasReports) Assert.That(round.Reports!.DeclaredCount, Is.Zero);
        Assert.That(archive.AtEnd, Is.True);
    }

    [Test]
    public void Decode_DecodesRelease1301RoundAndCharacterEnvelope()
    {
        const string payload =
            "AgIIQAAAAAAKMwwCAgxAAAAAAA4gNyIQQAAAgL8SQAAAgL8UEP7GEADIID8cykAE8O9BzEAAAAAAzhAC0AKkAwUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACoQW5ErIEAAAB/sYEAAAB/tSH8AQAAAAA=";
        var archive = new BitArchiveReader(Convert.FromBase64String(payload), 897);
        var context = new FieldDecodeContext();

        var value = Decoder().Decode(ref context, archive);
        var rounds = (CombatReportArrayUpdate<CombatRoundReportUpdate>)value.ObjectValue!;
        var report = rounds.Updates.Single().Reports!.Updates.Single();

        Assert.Multiple(() =>
        {
            Assert.That(rounds.Updates.Single().RoundNumber, Is.Zero);
            Assert.That(report.RoundNumber, Is.Zero);
            Assert.That(report.Died, Is.False);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void Decode_DecodesParticipantDamageUpdates()
    {
        const string payload =
            "AgIKVRwCAg4gcy4QQG6awUISQAAAQEIUEAQWtRgCAhiRBCUAAAA3YmVhMzNjMC02N2JhLTViODgtYjY1NS01ODI2ZTcwODkzZDMAGsIIAAAApMrIAAAAAAA4QJZgPEDmXECA3DSDhUSAAACAhEggCEyAADggglCAAgAAAFSAAAAAAFiAAAAAAFwEwCgAGQgAGgE2tQgCAjggcy46QG6awUI8QAAAQEI+EARAIHMuQkBumsFCREAAAEBCRhAESCArMFqlAgICXBAAXkABAAAAYEAAHBBBYgLIBJhBAAAAAAAIAwUAAAAAAAAAAAAA";
        var archive = new BitArchiveReader(Convert.FromBase64String(payload), 1890);
        var context = new FieldDecodeContext();

        var value = Decoder().Decode(ref context, archive);
        var participants = ((CombatReportArrayUpdate<CombatRoundReportUpdate>)value.ObjectValue!)
            .Updates.SelectMany(round => round.Reports?.Updates ?? [])
            .SelectMany(report => report.Interactions?.Updates ?? [])
            .ToArray();

        Assert.That(participants, Is.Not.Empty);
        Assert.That(participants.Any(item => item.Subject is not null), Is.True);
        Assert.That(archive.AtEnd, Is.True);
    }

    private static IFieldDecoder Decoder() => new CombatRoundReportsDecoder();
}
