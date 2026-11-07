using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

public sealed class ObfuscatedPlayerInformation : ExportGroupDescriptor<ObfuscatedPlayerInformation>
{
    public RiotUniqueNetId? SubjectUniqueId { get; set; }
    public bool? IsAfk { get; set; }
    public byte? ConnectionStatus { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(30, x => x.SubjectUniqueId).Decode(RiotUniqueNetIdDecoder.Instance);
        AddPropertyHandle(31, "bIsAfk", x => x.IsAfk).Bool();
        AddPropertyHandle(32, x => x.ConnectionStatus).EnumRemainingBits();
    }
}
