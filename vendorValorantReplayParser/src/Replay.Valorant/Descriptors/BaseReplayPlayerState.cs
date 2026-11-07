using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

public sealed class BaseReplayPlayerState : ExportGroupDescriptor<BaseReplayPlayerState>
{
    public override string Path => "/Game/GameModes/Common/BaseReplayPlayerState.BaseReplayPlayerState_C";
    public override ExportCategory Categories => ExportCategory.Movement;
    public override ExportGroupKind Kind => ExportGroupKind.Unknown;

    public NetObjectReference RemoteRole { get; set; }
    public NetObjectReference Owner { get; set; }
    public bool OnlySpectator { get; set; }
    public ObfuscatedPlayerInformation?[]? AllPlayersObfuscatedPlayerInformation { get; set; }
    public RiotUniqueNetId? SubjectUniqueId { get; set; }
    public bool IsAfk { get; set; }
    public byte ConnectionStatus { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.RemoteRole).ObjectNetGuid();
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty("bOnlySpectator", x => x.OnlySpectator).Bool();
        AddProperty(x => x.AllPlayersObfuscatedPlayerInformation)
            .RepLayoutDynamicArray<ObfuscatedPlayerInformation>();
        AddProperty(x => x.SubjectUniqueId)
            .Decode(RiotUniqueNetIdDecoder.Instance);
        AddProperty("bIsAfk", x => x.IsAfk).Bool();
        AddProperty(x => x.ConnectionStatus).EnumByte();
    }
}
