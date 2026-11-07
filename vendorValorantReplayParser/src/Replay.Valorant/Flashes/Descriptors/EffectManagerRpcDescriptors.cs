using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;
using Replay.Valorant.Combat;

namespace Replay.Valorant.Flashes.Descriptors;

public abstract class EffectManagerPlayParameters<T> : ExportGroupDescriptor<T>, IEffectDataPayload, IDecodedPayloadEventEmitter
    where T : EffectManagerPlayParameters<T>
{
    public override ExportCategory Categories => ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public NetObjectReference? EffectContainer { get; set; }
    /// <summary>Wire object name resolved from the GUID cache; null when absent/unresolved.</summary>
    public string? EffectContainerPath { get; set; }
    public NetObjectReference? WaitOnReplicationActor { get; set; }
    public EffectManagerFunctionFloatValue[]? FunctionFloatValues { get; set; }
    public EffectManagerFunctionVectorValue[]? FunctionVectorValues { get; set; }
    public EffectManagerFunctionObjectValue[]? FunctionObjectValues { get; set; }
    public FVector? Translation { get; set; }
    public FQuat? Rotation { get; set; }
    public FVector? Scale3D { get; set; }
    public string? AttachSocket { get; set; }
    public NetObjectReference? ClientControllerThatTriggered { get; set; }
    public float? StartMovementTime { get; set; }
    public EAresAlliance? AllianceFilter { get; set; }

    public IReadOnlyList<EffectManagerFloatValue> FloatValues =>
        EffectManagerValueMapper.Map(FunctionFloatValues);

    public IReadOnlyList<EffectManagerObjectValue> ObjectValues =>
        EffectManagerValueMapper.Map(FunctionObjectValues);

    public void EmitDecodedEvents(ref FieldDecodeContext context)
    {
        if (HasDecoded(nameof(EffectContainer)) && EffectContainer is { } guid &&
            context.NetGuidCache?.TryGetPath(guid.NetGuid, out var path) == true) EffectContainerPath = path;
    }

    protected void AddSharedFields()
    {
        AddPropertyHandle(0, x => x.EffectContainer).ObjectNetGuid();
        AddPropertyHandle(1, x => x.WaitOnReplicationActor).ObjectNetGuid();
        AddPropertyHandle(2, "FloatValues", x => x.FunctionFloatValues)
            .RepLayoutDynamicArray<EffectManagerFunctionFloatValue>();
        AddPropertyHandle(6, "VectorValues", x => x.FunctionVectorValues)
            .RepLayoutDynamicArray<EffectManagerFunctionVectorValue>();
        AddPropertyHandle(10, "ObjectValues", x => x.FunctionObjectValues)
            .RepLayoutDynamicArray<EffectManagerFunctionObjectValue>();
        AddPropertyHandle(22, "249", x => x.Rotation).NormalizedQuaternion();
        AddPropertyHandle(23, x => x.Translation).FVector();
        AddPropertyHandle(24, x => x.Scale3D).FVector();
        AddPropertyHandle(25, x => x.AttachSocket).FName();
    }

    protected void AddTriggerFields(uint firstHandle)
    {
        AddPropertyHandle(firstHandle, x => x.ClientControllerThatTriggered).ObjectNetGuid();
        AddPropertyHandle(firstHandle + 1, x => x.StartMovementTime).Float();
        AddPropertyHandle(firstHandle + 2, x => x.AllianceFilter).EnumRemainingBits();
    }
}

public sealed class EffectManagerPlayContinuousParameters : EffectManagerPlayParameters<EffectManagerPlayContinuousParameters>
{
    public override string Path => "/Script/ShooterGame.EffectManagerComponent:MulticastPlayContinuousEffect";

    public ulong? EffectId { get; set; }
    public string? SourceId { get; set; }
    public bool? IsLocalEffect { get; set; }
    public bool? IsTransient { get; set; }

    public IReadOnlyList<EffectManagerVectorValue> VectorValues =>
        EffectManagerValueMapper.Map(FunctionVectorValues);

    protected override void Configure()
    {
        AddSharedFields();
        AddPropertyHandle(26, "EffectID", x => x.EffectId).UInt64();
        AddPropertyHandle(27, "SourceID", x => x.SourceId).FName();
        AddPropertyHandle(28, "bLocalEffect", x => x.IsLocalEffect).Bool();
        AddPropertyHandle(29, "bTransient", x => x.IsTransient).Bool();
        AddTriggerFields(30);
    }
}

public sealed class EffectManagerPlayOneShotParameters : EffectManagerPlayParameters<EffectManagerPlayOneShotParameters>
{
    public override string Path => "/Script/ShooterGame.EffectManagerComponent:MulticastPlayOneShotEffect";

    protected override void Configure()
    {
        AddSharedFields();
        AddTriggerFields(26);
    }
}

public sealed class EffectManagerStopContinuousParameters
    : ExportGroupDescriptor<EffectManagerStopContinuousParameters>
{
    public override string Path => "/Script/ShooterGame.EffectManagerComponent:MulticastStopContinuousEffect";
    public override ExportCategory Categories => ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public ulong? EffectId { get; set; }
    public string? SourceId { get; set; }
    public bool? IsLocalEffect { get; set; }
    public bool? IsTransient { get; set; }
    public uint? StopEffectType { get; set; }
    public float? StopMovementTime { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(0, "EffectID", x => x.EffectId).UInt64();
        AddPropertyHandle(1, "SourceID", x => x.SourceId).FName();
        AddPropertyHandle(2, "bLocalEffect", x => x.IsLocalEffect).Bool();
        AddPropertyHandle(3, "bTransient", x => x.IsTransient).Bool();
        AddPropertyHandle(5, x => x.StopEffectType).EnumRemainingBits();
        AddPropertyHandle(6, x => x.StopMovementTime).Float();
    }
}

public sealed class EffectManagerUpdateContinuousParameters
    : ExportGroupDescriptor<EffectManagerUpdateContinuousParameters>, IEffectDataPayload
{
    public override string Path => "/Script/ShooterGame.EffectManagerComponent:MulticastUpdateContinuousEffect";
    public override ExportCategory Categories => ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public ulong? EffectId { get; set; }
    public string? SourceId { get; set; }
    public bool? IsLocalEffect { get; set; }
    public bool? IsTransient { get; set; }
    public NetObjectReference? WaitOnReplicationActor { get; set; }
    public EffectManagerUpdateFloatValue[]? UpdateFloatValues { get; set; }
    public EffectManagerUpdateVectorValue[]? UpdateVectorValues { get; set; }
    public EffectManagerUpdateObjectValue[]? UpdateObjectValues { get; set; }

    public IReadOnlyList<EffectManagerFloatValue> FloatValues =>
        EffectManagerValueMapper.Map(UpdateFloatValues);

    public IReadOnlyList<EffectManagerObjectValue> ObjectValues =>
        EffectManagerValueMapper.Map(UpdateObjectValues);

    protected override void Configure()
    {
        AddPropertyHandle(0, "EffectID", x => x.EffectId).UInt64();
        AddPropertyHandle(1, "SourceID", x => x.SourceId).FName();
        AddPropertyHandle(2, "bLocalEffect", x => x.IsLocalEffect).Bool();
        AddPropertyHandle(3, "bTransient", x => x.IsTransient).Bool();
        AddPropertyHandle(4, x => x.WaitOnReplicationActor).ObjectNetGuid();
        AddPropertyHandle(5, "FloatValues", x => x.UpdateFloatValues)
            .RepLayoutDynamicArray<EffectManagerUpdateFloatValue>();
        AddPropertyHandle(9, "VectorValues", x => x.UpdateVectorValues)
            .RepLayoutDynamicArray<EffectManagerUpdateVectorValue>();
        AddPropertyHandle(13, "ObjectValues", x => x.UpdateObjectValues)
            .RepLayoutDynamicArray<EffectManagerUpdateObjectValue>();
    }
}

public sealed class EffectManagerComponentClassNetCacheDescriptor
    : ClassNetCacheDescriptor<EffectManagerComponentClassNetCacheDescriptor>
{
    public override string Path => "/Script/ShooterGame.EffectManagerComponent_ClassNetCache";

    protected override void Configure()
    {
        AddFunctionHandle<EffectManagerPlayContinuousParameters>(
            0,
            "MulticastPlayContinuousEffect",
            "/Script/ShooterGame.EffectManagerComponent:MulticastPlayContinuousEffect",
            ExportCategory.Effects);
        AddFunctionHandle<EffectManagerPlayOneShotParameters>(
            1,
            "MulticastPlayOneShotEffect",
            "/Script/ShooterGame.EffectManagerComponent:MulticastPlayOneShotEffect",
            ExportCategory.Effects);
        AddFunctionHandle<EffectManagerStopContinuousParameters>(
            2,
            "MulticastStopContinuousEffect",
            "/Script/ShooterGame.EffectManagerComponent:MulticastStopContinuousEffect",
            ExportCategory.Effects);
        AddFunctionHandle<EffectManagerUpdateContinuousParameters>(
            3,
            "MulticastUpdateContinuousEffect",
            "/Script/ShooterGame.EffectManagerComponent:MulticastUpdateContinuousEffect",
            ExportCategory.Effects);
    }
}
