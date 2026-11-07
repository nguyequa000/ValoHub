using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.BountyHunter;

/// <summary>Observed Terror Trail actor exports. OptionalID is deliberately not interpreted.</summary>
public sealed class FadeTrailDescriptor : ExportGroupDescriptor<FadeTrailDescriptor>
{
    public const string TrailPath = "/Game/Characters/BountyHunter/S0/Global/Trails/Path_BountyHunter_Trail.Path_BountyHunter_Trail_C";
    public override string Path => TrailPath;
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;
    public NetObjectReference? Owner { get; set; }
    public NetObjectReference? Instigator { get; set; }
    public NetObjectReference? TrackedActor { get; set; }
    public bool? IsConsumed { get; set; }
    protected override void Configure()
    {
        AddProperty("Owner", x => x.Owner).ObjectNetGuid();
        AddProperty("Instigator", x => x.Instigator).ObjectNetGuid();
        AddProperty("TrackedActor", x => x.TrackedActor).ObjectNetGuid();
        AddProperty("bIsConsumed", x => x.IsConsumed).Bool();
    }
}

public sealed class FadeTrailClassNetCacheDescriptor : ClassNetCacheDescriptor<FadeTrailClassNetCacheDescriptor>
{
    public override string Path => FadeTrailDescriptor.TrailPath + "_ClassNetCache";
    protected override void Configure()
    {
        AddFunctionHandle<FadeTrailAddPointParameters>(1, "MulticastAddPoint",
            "/Script/ShooterGame.CustomPath:MulticastAddPoint", ExportCategory.Ability);
        AddFunctionHandle<FadeTrailInitializeParameters>(2, "MulticastInitializeFromPoints",
            "/Script/ShooterGame.CustomPath:MulticastInitializeFromPoints", ExportCategory.Ability);
    }
}

// Native ACustomPath declarations in VAL_12_07_DUMP/DUMP/ShooterGame_classes.h;
// parameter handles and inherited function paths verified in recorded replay exports.
public sealed class FadeTrailAddPointParameters : ExportGroupDescriptor<FadeTrailAddPointParameters>
{
    public override string Path => "/Script/ShooterGame.CustomPath:MulticastAddPoint";
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;
    public FVector? NewPoint { get; set; }
    protected override void Configure() => AddPropertyHandle(0, x => x.NewPoint).FVector();
}

public sealed class FadeTrailInitializeParameters : ExportGroupDescriptor<FadeTrailInitializeParameters>
{
    public override string Path => "/Script/ShooterGame.CustomPath:MulticastInitializeFromPoints";
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;
    public FadeTrailInitialPoint?[]? InitialPoints { get; set; }
    protected override void Configure() => AddPropertyHandle(0, x => x.InitialPoints)
        .RepLayoutDynamicArray<FadeTrailInitialPoint>();
}

public sealed class FadeTrailInitialPoint : ExportGroupDescriptor<FadeTrailInitialPoint>
{
    public override string Path => "/Script/ShooterGame.CustomPath:MulticastInitializeFromPoints:InitialPoints";
    public override ExportCategory Categories => ExportCategory.Ability;
    public FVector? Position { get; set; }
    protected override void Configure() => AddPropertyHandle(1, "InitialPoints", x => x.Position).FVector();
}
