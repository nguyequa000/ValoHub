using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Combat;

public abstract class LifeChangeEvent<T> : ExportGroupDescriptor<T> where T : LifeChangeEvent<T>
{
    public override ExportCategory Categories => ExportCategory.Gunplay;
    public NetObjectReference ChangedComponent { get; set; }
    public float LifeResult { get; set; }
    public float DeltaLife { get; set; }
    public bool AliveAfterChange { get; set; }

    protected void AddSharedFields(uint firstHandle)
    {
        AddPropertyHandle(firstHandle, x => x.ChangedComponent).ObjectNetGuid();
        AddPropertyHandle(firstHandle + 1, x => x.LifeResult).Float();
        AddPropertyHandle(firstHandle + 2, x => x.DeltaLife).Float();
        AddPropertyHandle(firstHandle + 3, "bAliveAfterChange", x => x.AliveAfterChange).Bool();
    }
}

public sealed class HealLifeChangeEvent : LifeChangeEvent<HealLifeChangeEvent>
{
    public override string Path => "/Script/ShooterGame.LifeChangeEvent:Heal";
    protected override void Configure() => AddSharedFields(2);
}

public abstract class HealParameters<T> : ExportGroupDescriptor<T> where T : HealParameters<T>
{
    public override ExportCategory Categories => ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;
    public float Amount { get; set; }
    public HealLifeChangeEvent?[]? LifeChanges { get; set; }
    public NetObjectReference EventInstigator { get; set; }
    public NetObjectReference EventInstigatorPawn { get; set; }
    public NetObjectReference Causer { get; set; }
    protected void AddSharedFields(string amountName, string causerName)
    {
        AddPropertyHandle(0, amountName, x => x.Amount).Float();
        AddPropertyHandle(1, "LifeChangeBySection", x => x.LifeChanges).RepLayoutDynamicArray<HealLifeChangeEvent>();
        AddPropertyHandle(7, x => x.EventInstigator).ObjectNetGuid();
        AddPropertyHandle(8, x => x.EventInstigatorPawn).ObjectNetGuid();
        AddPropertyHandle(9, causerName, x => x.Causer).ObjectNetGuid();
    }
}

public sealed class MulticastNotifyHealParameters : HealParameters<MulticastNotifyHealParameters>
{
    public override string Path => "/Script/ShooterGame.DamageableComponent:MulticastNotifyHeal";
    protected override void Configure() => AddSharedFields("HealTaken", "HealCauser");
}
public sealed class MulticastNotifyOverhealDecayParameters : HealParameters<MulticastNotifyOverhealDecayParameters>
{
    public override string Path => "/Script/ShooterGame.DamageableComponent:MulticastNotifyOverhealDecay";
    protected override void Configure() => AddSharedFields("DecayApplied", "DecayCauser");
}

public sealed class SectionLifeChangeEvent : LifeChangeEvent<SectionLifeChangeEvent>
{
    public override string Path => "/Script/ShooterGame.LifeChangeEvent:SectionLifeChange";
    protected override void Configure() => AddSharedFields(1);
}

public sealed class MulticastSectionLifeChangeParameters : ExportGroupDescriptor<MulticastSectionLifeChangeParameters>
{
    public override string Path => "/Script/ShooterGame.DamageableComponent:MulticastSectionLifeChange";
    public override ExportCategory Categories => ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;
    public SectionLifeChangeEvent?[]? LifeChanges { get; set; }
    public bool AliveOnServer { get; set; }
    public NetObjectReference Character { get; set; }
    public float NetTimestamp { get; set; }
    public int RespawnNumber { get; set; }
    public int LifeChangeEventIndex { get; set; }
    protected override void Configure()
    {
        AddPropertyHandle(0, "LifeChangeEvents", x => x.LifeChanges).RepLayoutDynamicArray<SectionLifeChangeEvent>();
        AddPropertyHandle(6, "bAliveOnServer", x => x.AliveOnServer).Bool();
        AddPropertyHandle(7, x => x.Character).ObjectNetGuid();
        AddPropertyHandle(8, x => x.NetTimestamp).Float();
        AddPropertyHandle(9, x => x.RespawnNumber).Int32();
        AddPropertyHandle(10, x => x.LifeChangeEventIndex).Int32();
    }
}
