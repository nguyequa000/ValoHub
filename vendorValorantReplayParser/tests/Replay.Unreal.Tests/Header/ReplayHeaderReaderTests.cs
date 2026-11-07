using System.Buffers.Binary;
using Replay.Encoding.Archives;
using Replay.Models.Errors;
using Replay.Models.Protocol;
using Replay.Models.Replay;
using Replay.Models.Unreal;
using Replay.Unreal.Header;

namespace Replay.Unreal.Tests.Header;

public class ReplayHeaderReaderTests
{
    private static readonly Guid HeaderGuid = Guid.Parse("00112233-4455-6677-8899-aabbccddeeff");
    private static readonly byte[] HeaderSkip12_10 = [3, 0, 0, 0, 49, 56, 0];
    private static readonly byte[] HeaderSkip12_11 = [2, 0, 0, 0, 57, 0];

    [Test]
    public void Read_ParsesSupportedValorantHeader()
    {
        var archive = new FBinaryArchive(BuildHeader());

        var result = new ReplayHeaderReader(archive).Read();

        Assert.Multiple(() =>
        {
            Assert.That(result.Header.NetworkVersion, Is.EqualTo(Constants.ExpectedNetworkVersion));
            Assert.That(result.Header.NetworkChecksum, Is.EqualTo(0x11223344u));
            Assert.That(result.Header.EngineNetworkProtocolVersion,
                Is.EqualTo(Constants.ExpectedEngineNetworkProtocolVersion));
            Assert.That(result.Header.GameNetworkProtocolVersion, Is.EqualTo(0x55667788u));
            Assert.That(result.Header.Guid, Is.EqualTo(HeaderGuid));
            Assert.That(result.ReplayVersion.Major, Is.EqualTo(12));
            Assert.That(result.ReplayVersion.Minor, Is.EqualTo(10));
            Assert.That(result.ReplayVersion.Patch, Is.EqualTo(1));
            Assert.That(result.ReplayVersion.Changelist, Is.EqualTo(123456u));
            Assert.That(result.ReplayVersion.Branch, Is.EqualTo("++Ares-Core+release-12.10"));
            Assert.That(result.UEVersion.UE4Version, Is.EqualTo(1001u));
            Assert.That(result.UEVersion.UE5Version, Is.EqualTo(1002u));
            Assert.That(result.UEVersion.PackageVersionLicense, Is.EqualTo(1003u));
            Assert.That(result.Header.LevelNamesAndTimes, Is.EqualTo(new[] { ("Ascent", 42u) }));
            Assert.That(result.Header.Flags,
                Is.EqualTo(ReplayHeaderFlags.HasStreamingFixes | ReplayHeaderFlags.GameSpecificFrameData));
            Assert.That(result.Header.GameSpecificData, Is.EqualTo(new[] { "valorant", "competitive" }));
            Assert.That(result.Header.MinRecordHz, Is.EqualTo(15.0f));
            Assert.That(result.Header.MaxRecordHz, Is.EqualTo(30.0f));
            Assert.That(result.Header.FrameLimitInMs, Is.EqualTo(33.3f));
            Assert.That(result.Header.CheckpointLimitInMs, Is.EqualTo(250.0f));
            Assert.That(result.Header.Platform, Is.EqualTo("Windows"));
            Assert.That(result.Header.BuildConfig, Is.EqualTo(7));
            Assert.That(result.Header.BuildTargetType, Is.EqualTo(BuildTargetType.Client));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void Read_ParsesAlternateValorantHeaderSkipBytes()
    {
        var archive = new FBinaryArchive(BuildHeader(replayVersionSkipBytes: HeaderSkip12_11));

        var result = new ReplayHeaderReader(archive).Read();

        Assert.Multiple(() =>
        {
            Assert.That(result.Header.NetworkVersion, Is.EqualTo(Constants.ExpectedNetworkVersion));
            Assert.That(result.ReplayVersion.Branch, Is.EqualTo("++Ares-Core+release-12.10"));
            Assert.That(result.UEVersion.UE4Version, Is.EqualTo(1001u));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void Read_InvalidNetworkMagic_ThrowsInvalidReplayHeaderException()
    {
        var bytes = BuildHeader(networkMagic: 0xDEADBEEFu);
        var archive = new FBinaryArchive(bytes);

        Assert.Throws<InvalidReplayHeaderException>(() => new ReplayHeaderReader(archive).Read());
    }

    [Test]
    public void Read_NegativeCustomVersionCount_ThrowsInvalidReplayHeaderException()
    {
        var bytes = BuildHeader(customVersionCount: -1);
        var archive = new FBinaryArchive(bytes);

        Assert.Throws<InvalidReplayHeaderException>(() => new ReplayHeaderReader(archive).Read());
    }

    // Real header bytes of the authorised 11.07 (Sunset) replay: the 265-byte header chunk.
    // No version-extension block: UE4Version (522) directly follows the replay version.
    private const string Real1107HeaderHex =
        "3da1f52c1300000003000000a35c9162f74b8e1cc7120ea3f79d21c820000000240d40cc7b4ee9e083a2f99b27c0c0dc000000008a991784ec43c0bb19d1b38122272d07130000001414c9d62000000000000000ab252cf2e34cee2977bb0aa99bf544a20500030002001f8a3a801a0000002b2b417265732d436f72652b72656c656173652d31312e3037000a020000f10300004d000000010000001b0000002f47616d652f4d6170732f4a756c696574742f4a756c69657474000000000002000000010000001c0000007b0a092273657269616c697a656456657273696f6e223a20310a7d000000000000007a44000080bf000080bf0c0000004c696e7578536572766572000402";

    // Real leading bytes (through the three UE versions) of the 13.06 (Haven) and 13.08 (Pearl)
    // replay headers: extension block of length 3 ("18\0" / "19\0"), then UE4Version 522.
    // The player-loadout JSON from the real tail is intentionally not embedded.
    private const string Real1306PrefixHex =
        "3da1f52c1300000003000000a35c9162f74b8e1cc7120ea3f79d21c820000000240d40cc7b4ee9e083a2f99b27c0c0dc000000008a991784ec43c0bb19d1b38122272d07140000001256d5e92000000000000000faf29e9f5549e5a6be3661a4ef1b0269050003000200f14b55801a0000002b2b417265732d436f72652b72656c656173652d31332e303600030000003138000a020000f103000050000000";
    private const string Real1308PrefixHex =
        "3da1f52c1300000003000000a35c9162f74b8e1cc7120ea3f79d21c820000000240d40cc7b4ee9e083a2f99b27c0c0dc000000008a991784ec43c0bb19d1b38122272d071400000021210a09200000000000000093af0d08994f349957247598a6738bc205000300020058f556801a0000002b2b417265732d436f72652b72656c656173652d31332e303800030000003139000a020000f103000050000000";

    [Test]
    public void Read_Real1107LegacyHeader_ReadsUe4VersionWithoutExtensionBlock()
    {
        var bytes = Convert.FromHexString(Real1107HeaderHex);
        Assert.That(bytes, Has.Length.EqualTo(265));
        var archive = new FBinaryArchive(bytes);

        var result = new ReplayHeaderReader(archive).Read();

        Assert.Multiple(() =>
        {
            Assert.That(result.ReplayVersion.Branch, Is.EqualTo("++Ares-Core+release-11.07"));
            Assert.That(result.UEVersion.UE4Version, Is.EqualTo(522u));
            Assert.That(result.UEVersion.UE5Version, Is.EqualTo(1009u));
            Assert.That(result.UEVersion.PackageVersionLicense, Is.EqualTo(77u));
            Assert.That(result.Header.LevelNamesAndTimes,
                Is.EqualTo(new[] { ("/Game/Maps/Juliett/Juliett", 0u) }));
            Assert.That(result.Header.Platform, Is.EqualTo("LinuxServer"));
            Assert.That(result.Header.BuildConfig, Is.EqualTo(4));
            Assert.That(result.Header.BuildTargetType, Is.EqualTo((BuildTargetType)2));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase(Real1306PrefixHex, "++Ares-Core+release-13.06", 80u)]
    [TestCase(Real1308PrefixHex, "++Ares-Core+release-13.08", 80u)]
    public void Read_RealModernHeaderPrefix_SkipsExtensionBlockAndKeepsUe4Version522(
        string prefixHex, string branch, uint license)
    {
        var bytes = new List<byte>(Convert.FromHexString(prefixHex));
        AppendHeaderTail(bytes);
        var archive = new FBinaryArchive(bytes.ToArray());

        var result = new ReplayHeaderReader(archive).Read();

        Assert.Multiple(() =>
        {
            Assert.That(result.ReplayVersion.Branch, Is.EqualTo(branch));
            Assert.That(result.UEVersion.UE4Version, Is.EqualTo(522u));
            Assert.That(result.UEVersion.UE5Version, Is.EqualTo(1009u));
            Assert.That(result.UEVersion.PackageVersionLicense, Is.EqualTo(license));
            Assert.That(result.Header.LevelNamesAndTimes, Is.EqualTo(new[] { ("Ascent", 42u) }));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase(521)]
    [TestCase(523)]
    public void Read_ExtensionLengthNextToLegacySentinel_IsStillSkippedAsModern(int extensionLength)
    {
        // Only the exact word 522 selects the legacy layout; neighbours are ordinary lengths.
        var archive = new FBinaryArchive(BuildHeader(
            replayVersionSkipBytes: ExtensionBlock(extensionLength)));

        var result = new ReplayHeaderReader(archive).Read();

        Assert.Multiple(() =>
        {
            Assert.That(result.UEVersion.UE4Version, Is.EqualTo(1001u));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase(522u + 100u)]
    [TestCase(uint.MaxValue)]
    public void Read_ExtensionLengthBeyondHeader_ThrowsInvalidReplayHeaderException(uint declared)
    {
        // Declared length exceeds what remains: must be rejected, never clamped or reinterpreted.
        var bytes = new List<byte>();
        AddUInt32(bytes, declared);
        bytes.AddRange(new byte[8]);
        var archive = new FBinaryArchive(BuildHeaderWithRawVersionBytes(bytes.ToArray()));

        Assert.Throws<InvalidReplayHeaderException>(() => new ReplayHeaderReader(archive).Read());
    }

    [Test]
    public void Read_Real1107HeaderTruncatedAtEveryBoundary_ThrowsInvalidReplayHeaderException()
    {
        var full = Convert.FromHexString(Real1107HeaderHex);

        for (var length = 0; length < full.Length; length++)
        {
            var truncated = full.AsSpan(0, length).ToArray();
            var archive = new FBinaryArchive(truncated);
            Assert.Throws<InvalidReplayHeaderException>(
                () => new ReplayHeaderReader(archive).Read(),
                $"truncation at {length} of {full.Length} bytes");
        }
    }

    [Test]
    public void Read_LegacySentinelWithNoFollowingUeVersions_ThrowsInvalidReplayHeaderException()
    {
        // Header ends exactly after the 522 word: the legacy path must not read past the archive.
        var prefix = Convert.FromHexString(Real1107HeaderHex)[..144];
        var archive = new FBinaryArchive(prefix);

        Assert.Throws<InvalidReplayHeaderException>(() => new ReplayHeaderReader(archive).Read());
    }

    private static byte[] ExtensionBlock(int length)
    {
        var bytes = new List<byte>();
        AddUInt32(bytes, (uint)length);
        bytes.AddRange(new byte[length]);
        return bytes.ToArray();
    }

    private static byte[] BuildHeaderWithRawVersionBytes(byte[] raw)
    {
        // BuildHeader writes UE versions and tail after the supplied bytes; this variant lets a
        // test supply the bytes that follow the replay version verbatim and nothing else.
        var bytes = new List<byte>(BuildHeader(replayVersionSkipBytes: []));
        var marker = bytes.Count;
        // Locate the first UE4Version word (1001) after the replay version and cut there.
        var needle = BitConverter.GetBytes(1001u);
        var index = bytes.ToArray().AsSpan().IndexOf(needle);
        bytes.RemoveRange(index, marker - index);
        bytes.AddRange(raw);
        return bytes.ToArray();
    }

    private static byte[] BuildHeader(
        uint networkMagic = Constants.NetworkMagic,
        int customVersionCount = 0,
        byte[]? replayVersionSkipBytes = null)
    {
        var bytes = new List<byte>();
        AddUInt32(bytes, networkMagic);
        AddUInt32(bytes, Constants.ExpectedNetworkVersion);
        AddInt32(bytes, customVersionCount);
        for (var i = 0; i < customVersionCount; i++)
        {
            bytes.AddRange(new byte[20]);
        }

        AddUInt32(bytes, 0x11223344u);
        AddUInt32(bytes, Constants.ExpectedEngineNetworkProtocolVersion);
        AddUInt32(bytes, 0x55667788u);
        AddUnrealGuid(bytes, 0x00112233u, 0x44556677u, 0x8899AABBu, 0xCCDDEEFFu);

        AddUInt16(bytes, 12);
        AddUInt16(bytes, 10);
        AddUInt16(bytes, 1);
        AddUInt32(bytes, 123456u);
        AddFString(bytes, "++Ares-Core+release-12.10");

        bytes.AddRange(replayVersionSkipBytes ?? HeaderSkip12_10);

        AddUInt32(bytes, 1001u);
        AddUInt32(bytes, 1002u);
        AddUInt32(bytes, 1003u);

        AppendHeaderTail(bytes);
        return bytes.ToArray();
    }

    private static void AppendHeaderTail(List<byte> bytes)
    {
        AddInt32(bytes, 1);
        AddFString(bytes, "Ascent");
        AddUInt32(bytes, 42u);

        AddUInt32(bytes, (uint)(ReplayHeaderFlags.HasStreamingFixes | ReplayHeaderFlags.GameSpecificFrameData));

        AddInt32(bytes, 2);
        AddFString(bytes, "valorant");
        AddFString(bytes, "competitive");

        AddSingle(bytes, 15.0f);
        AddSingle(bytes, 30.0f);
        AddSingle(bytes, 33.3f);
        AddSingle(bytes, 250.0f);
        AddFString(bytes, "Windows");
        bytes.Add(7);
        bytes.Add((byte)BuildTargetType.Client);
    }

    private static void AddFString(List<byte> bytes, string value)
    {
        var encoded = System.Text.Encoding.UTF8.GetBytes(value + '\0');
        AddInt32(bytes, encoded.Length);
        bytes.AddRange(encoded);
    }

    private static void AddUInt16(List<byte> bytes, ushort value)
    {
        Span<byte> buffer = stackalloc byte[2];
        BinaryPrimitives.WriteUInt16LittleEndian(buffer, value);
        bytes.AddRange(buffer.ToArray());
    }

    private static void AddUInt32(List<byte> bytes, uint value)
    {
        Span<byte> buffer = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(buffer, value);
        bytes.AddRange(buffer.ToArray());
    }

    private static void AddInt32(List<byte> bytes, int value)
    {
        Span<byte> buffer = stackalloc byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(buffer, value);
        bytes.AddRange(buffer.ToArray());
    }

    private static void AddSingle(List<byte> bytes, float value)
    {
        AddUInt32(bytes, BitConverter.SingleToUInt32Bits(value));
    }

    private static void AddUnrealGuid(List<byte> bytes, uint a, uint b, uint c, uint d)
    {
        AddUInt32(bytes, a);
        AddUInt32(bytes, b);
        AddUInt32(bytes, c);
        AddUInt32(bytes, d);
    }
}
