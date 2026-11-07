using Replay.Models.Descriptors;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Terra;

/// <summary>One observed world-space point in Waylay Refract's replicated return route.</summary>
public sealed class WaylayRewindPathPointDescriptor : ExportGroupDescriptor<WaylayRewindPathPointDescriptor>
{
    public override string Path =>
        "/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C:RewindPathPoint";

    public override ExportCategory Categories => ExportCategory.Ability;

    public FVector? Position { get; set; }

    protected override void Configure() => AddPropertyHandle(64, x => x.Position).FVector();
}
