using Replay.Models.Descriptors;
using Replay.Models.Replay;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors;

namespace Replay.Valorant.GameState;

public sealed class RoundParticipantsInfo(uint roundNumberHandle) : ExportGroupDescriptor<RoundParticipantsInfo>
{
    public int? RoundNumber { get; set; }
    public ReplicatedObjectReference?[]? PenalizedPlayers { get; set; }
    public override object CreatePayloadInstance() => new RoundParticipantsInfo(roundNumberHandle);

    protected override void Configure()
    {
        AddPropertyHandle(roundNumberHandle, x => x.RoundNumber).Int32();
        AddPropertyHandle(roundNumberHandle + 1, x => x.PenalizedPlayers).RepLayoutDynamicArray(
            new VersionedDefinition<ReplicatedObjectReference>(new(roundNumberHandle + 2, "PenalizedPlayers")));
    }
}
