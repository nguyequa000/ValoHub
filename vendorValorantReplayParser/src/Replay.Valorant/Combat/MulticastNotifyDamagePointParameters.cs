using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Combat;

public sealed class MulticastNotifyDamagePointParameters : PointDamageParameters<MulticastNotifyDamagePointParameters>
{
    public override string Path => "/Script/ShooterGame.DamageableComponent:MulticastNotifyDamage_Point";

    public DamageAssist?[]? AssistsList { get; set; }
    public byte? AssistType { get; set; }
    public NetObjectReference Assister { get; set; }
    public NetObjectReference AssistingEquippableClass { get; set; }
    public Replay.Models.Unreal.FGameplayTag? AssistTag { get; set; }
    public int KillsForKiller { get; set; }
    public int KillsForVictim { get; set; }
    public NetObjectReference DeathAnimMontage { get; set; }
    public NetObjectReference? DeathMontageEffectOverride { get; set; }
    public NetObjectReference? DeathMontageEffectOverrideContext { get; set; }
    public bool DeathMontageEffectOverrideIsQueued { get; set; }

    protected override void Configure()
    {
        AddSharedFields();
        AddPointFields();
        AddPropertyHandle(34, x => x.AssistsList).RepLayoutDynamicArray<DamageAssist>();
        AddPropertyHandle(35, x => x.AssistType).EnumRemainingBits();
        AddPropertyHandle(36, x => x.Assister, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(37, x => x.AssistingEquippableClass, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(38, x => x.AssistTag).FGameplayTag();
        AddPropertyHandle(40, x => x.KillsForKiller, ExportCategory.Gunplay).Int32();
        AddPropertyHandle(41, x => x.KillsForVictim, ExportCategory.Gunplay).Int32();
        AddPropertyHandle(42, x => x.DeathAnimMontage, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(43, x => x.DeathMontageEffectOverride).ObjectNetGuid();
        AddPropertyHandle(44, x => x.DeathMontageEffectOverrideContext).ObjectNetGuid();
        AddPropertyHandle(45, "bDeathMontageEffectOverrideIsQueued", x => x.DeathMontageEffectOverrideIsQueued, ExportCategory.Gunplay).Bool();
    }
}