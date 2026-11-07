using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Replay;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors;

namespace Replay.Valorant.Combat;

public abstract class DamageParameters<T> : ExportGroupDescriptor<T>
    where T : DamageParameters<T>
{
    public override ExportCategory Categories => ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public float DamageTaken { get; set; }
    public float DamageDealt { get; set; }
    public bool DamageKilledTarget { get; set; }
    public bool AliveAfterDamage { get; set; }
    public NetObjectReference EventInstigator { get; set; }
    public NetObjectReference DamageCauser { get; set; }
    public FVector? DamageOrigin { get; set; }
    public NetObjectReference? EquippableUsed { get; set; }
    public NetObjectReference DamageType { get; set; }
    public DamageLifeChangeEvent?[]? LifeChangeEvents { get; set; }
    public NetObjectReference ChangedComponent { get; set; }
    public float? LifeResult { get; set; }
    public float DeltaLife { get; set; }
    public bool AliveAfterChange { get; set; }
    public NetObjectReference EventInstigatorPawn { get; set; }
    public NetObjectReference DamagerPlayerState { get; set; }
    public NetObjectReference KillCreditPlayerState { get; set; }
    public EAresRegionalDamage? RegionalDamage { get; set; }
    public NetObjectReference Character { get; set; }
    public float NetTimestamp { get; set; }
    public int RespawnNumber { get; set; }
    public int LifeChangeEventIndex { get; set; }
    public int VictimRespawnNumber { get; set; }
    public bool EquippableUsedZoomed { get; set; }
    public bool EquippableUsedInFocusMode { get; set; }

    protected void AddSharedFields(uint offset = 0)
    {
        AddPropertyHandle(offset, x => x.DamageTaken, ExportCategory.Gunplay).Float();
        AddPropertyHandle(1 + offset, x => x.DamageDealt, ExportCategory.Gunplay).Float();
        AddPropertyHandle(2 + offset, "bDamageKilledTarget", x => x.DamageKilledTarget, ExportCategory.Gunplay).Bool();
        AddPropertyHandle(3 + offset, "bAliveAfterDamage", x => x.AliveAfterDamage, ExportCategory.Gunplay).Bool();
        AddPropertyHandle(4 + offset, x => x.EventInstigator, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(5 + offset, x => x.DamageCauser, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(6 + offset, x => x.DamageOrigin, ExportCategory.Gunplay).FVectorNetQuantize100();
        AddPropertyHandle(7 + offset, x => x.EquippableUsed, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(8 + offset, x => x.DamageType, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(9 + offset, x => x.LifeChangeEvents, ExportCategory.Gunplay).RepLayoutDynamicArray(
            new VersionedDefinition<DamageLifeChangeEvent>(new(offset)));
        AddPropertyHandle(10 + offset, x => x.ChangedComponent, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(11 + offset, x => x.LifeResult, ExportCategory.Gunplay).Float();
        AddPropertyHandle(12 + offset, x => x.DeltaLife, ExportCategory.Gunplay).Float();
        AddPropertyHandle(13 + offset, "bAliveAfterChange", x => x.AliveAfterChange, ExportCategory.Gunplay).Bool();
        AddPropertyHandle(15 + offset, x => x.EventInstigatorPawn, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(16 + offset, x => x.DamagerPlayerState, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(17 + offset, x => x.KillCreditPlayerState, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(18 + offset, x => x.RegionalDamage, ExportCategory.Gunplay).EnumByte();
        AddPropertyHandle(19 + offset, x => x.Character, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(20 + offset, x => x.NetTimestamp, ExportCategory.Gunplay).Float();
        AddPropertyHandle(21 + offset, x => x.RespawnNumber, ExportCategory.Gunplay).Int32();
        AddPropertyHandle(22 + offset, x => x.LifeChangeEventIndex, ExportCategory.Gunplay).Int32();
        AddPropertyHandle(23 + offset, x => x.VictimRespawnNumber, ExportCategory.Gunplay).Int32();
        AddPropertyHandle(24 + offset, "bEquippableUsedZoomed", x => x.EquippableUsedZoomed, ExportCategory.Gunplay).Bool();
        AddPropertyHandle(25 + offset, "bEquippableUsedInFocusMode", x => x.EquippableUsedInFocusMode, ExportCategory.Gunplay).Bool();
    }
}
