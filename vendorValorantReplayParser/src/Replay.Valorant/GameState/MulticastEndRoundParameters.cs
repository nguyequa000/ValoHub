using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.GameState;

public sealed class MulticastEndRoundParameters : ExportGroupDescriptor<MulticastEndRoundParameters>
{
    public override string Path => "/Script/ShooterGame.ShooterGameState:MulticastEndRound";
    public override ExportCategory Categories => ExportCategory.GameState;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public int NewRoundNumber { get; set; }
    public byte? Type { get; set; }
    public MatchPointTeam?[]? TeamsOnMatchPoint { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(0, x => x.NewRoundNumber, ExportCategory.GameState).Int32();
        AddPropertyHandle(1, x => x.Type).EnumRemainingBits();
        AddPropertyHandle(2, x => x.TeamsOnMatchPoint).RepLayoutDynamicArray<MatchPointTeam>();
    }
}

public sealed class MatchPointTeam : ExportGroupDescriptor<MatchPointTeam>
{
    public int? Team { get; set; }
    protected override void Configure() => AddPropertyHandle(3, "TeamsOnMatchPoint", x => x.Team).Int32();
}
