using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;
using Replay.Valorant.Combat;

namespace Replay.Valorant.Descriptors.Effects.Replay;

public sealed class ReplayPlayContinuousEffectAtLocationParameters
    : ExportGroupDescriptor<ReplayPlayContinuousEffectAtLocationParameters>
{
    public override string Path => "/Script/ShooterGame.ReplayEffectComponent:ReplayPlayContinuousEffectAtLocation";
    public override ExportCategory Categories => ExportCategory.Effects | ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public NetObjectReference? EffectContainer { get; set; }
    public ulong? EffectId { get; set; }
    public string? SourceId { get; set; }
    public bool? IsLocalEffect { get; set; }
    public bool? IsTransient { get; set; }
    public NetObjectReference? WaitOnReplicationActor { get; set; }
    public EffectDataFloat[]? FloatValues { get; set; }
    public EffectDataVector[]? VectorValues { get; set; }
    public EffectDataObject[]? ObjectValues { get; set; }
    public FVector? Location { get; set; }
    public FRotator? Rotation { get; set; }
    public EAresAlliance? AllianceFilter { get; set; }
    public float? StartMovementTime { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(0, x => x.EffectContainer).ObjectNetGuid();
        AddPropertyHandle(1, "EffectID", x => x.EffectId, ExportCategory.Effects).UInt64();
        AddPropertyHandle(2, "SourceID", x => x.SourceId, ExportCategory.Effects).FName();
        AddPropertyHandle(3, "bLocalEffect", x => x.IsLocalEffect, ExportCategory.Effects).Bool();
        AddPropertyHandle(4, "bTransient", x => x.IsTransient, ExportCategory.Effects).Bool();
        AddPropertyHandle(5, x => x.WaitOnReplicationActor, ExportCategory.Effects).ObjectNetGuid();
        AddPropertyHandle(6, x => x.FloatValues, ExportCategory.Gunplay).RepLayoutDynamicArray<EffectDataFloat>();
        AddPropertyHandle(10, x => x.VectorValues, ExportCategory.Gunplay).RepLayoutDynamicArray<EffectDataVector>();
        AddPropertyHandle(14, x => x.ObjectValues, ExportCategory.Gunplay).RepLayoutDynamicArray<EffectDataObject>();
        AddPropertyHandle(26, x => x.Location, ExportCategory.Effects | ExportCategory.Gunplay).FVector();
        AddPropertyHandle(27, x => x.Rotation, ExportCategory.Effects | ExportCategory.Gunplay).FRotatorShort();
        AddPropertyHandle(28, x => x.AllianceFilter, ExportCategory.Effects).EnumRemainingBits();
        AddPropertyHandle(29, x => x.StartMovementTime, ExportCategory.Effects | ExportCategory.Gunplay).Float();
    }
}
