namespace Replay.Models.Events;

/// <summary>A native object GUID/path export. PacketId is -1 for frame-level exports.</summary>
public sealed record NetGuidExported(
    float TimeSeconds,
    int PacketId,
    uint NetGuid,
    string Path,
    uint OuterNetGuid)
    : ReplayEvent(TimeSeconds, PacketId);
