using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Breach;

/// <summary>Recorded Fault Line SetSizeFromCharge RPC, parameter handle 0.</summary>
public sealed class BreachFaultLineChargeParameters : ExportGroupDescriptor<BreachFaultLineChargeParameters>
{
    public const string ActorPath = "/Game/Characters/Breach/S0/Ability_E/GameObject_Breach_E_SweetSpotFissure.GameObject_Breach_E_SweetSpotFissure_C";
    public override string Path => ActorPath + ":SetSizeFromCharge";
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;
    public double? ChargePercent { get; set; }
    protected override void Configure() => AddPropertyHandle(0, x => x.ChargePercent).Double();
}

public sealed class BreachFaultLineClassNetCacheDescriptor : ClassNetCacheDescriptor<BreachFaultLineClassNetCacheDescriptor>
{
    public override string Path => BreachFaultLineChargeParameters.ActorPath + "_ClassNetCache";
    protected override void Configure() => AddFunctionHandle<BreachFaultLineChargeParameters>(1, "SetSizeFromCharge",
        BreachFaultLineChargeParameters.ActorPath + ":SetSizeFromCharge", ExportCategory.Ability);
}
