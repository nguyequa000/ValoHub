using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

public sealed record RiotUniqueNetId(string Type, string Value);

internal sealed class RiotUniqueNetIdDecoder : IFieldDecoder
{
    public static RiotUniqueNetIdDecoder Instance { get; } = new();

    public DecodedFieldValue Decode(ref FieldDecodeContext context, FBitArchive archive)
    {
        var flags = archive.ReadByte();
        // FUniqueNetIdRepl: encoded + empty, with no type or identifier following.
        if (flags == 3) return DecodedFieldValue.FromObject(new RiotUniqueNetId("Riot", string.Empty));
        // VALORANT uses an unencoded string with the explicit (type hash 31) Riot type.
        if (flags != 0xF8) throw Invalid(archive, flags, "Unsupported VALORANT unique ID encoding.");
        var type = archive.ReadFString();
        if (type != "Riot") throw Invalid(archive, flags, $"Unsupported unique ID type '{type}'.");
        return DecodedFieldValue.FromObject(new RiotUniqueNetId(type, archive.ReadFString()));
    }

    private static ArchiveReadException Invalid(FBitArchive archive, long requested, string message) =>
        new(ArchiveErrorCode.InvalidData, nameof(RiotUniqueNetIdDecoder), archive.Position, archive.Length, requested, message);
}
