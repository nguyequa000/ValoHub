using Replay.Models.Descriptors;
using Replay.Valorant.Smokes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Smonk;

public static class SmonkDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new SmonkAgentDescriptor(),
            new SmonkSmokeAbilityDescriptor(),
            new SmonkPostDeathSmokeAbilityDescriptor(),
            new SmonkSmokeDescriptor(),
            new SmonkPersistentSmokeDescriptor(),
            Equipment("/Game/Characters/Smonk/S0/Ability_4/ReactiveArmor/Ability_Smonk_BFArmor_Reactive.Ability_Smonk_BFArmor_Reactive_C"),
            Equipment("/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/Ability_Q_Smonk_DebuffKnife.Ability_Q_Smonk_DebuffKnife_C"),
            Owned("/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/GameObject_Smonk_Q_DecayExplosion.GameObject_Smonk_Q_DecayExplosion_C"),
            Projectile("/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/Projectile_Smonk_DecayNade.Projectile_Smonk_DecayNade_C"),
            Equipment("/Game/Characters/Smonk/S0/Ability_X/ReactiveRes/Ability_Smonk_X_PostDeath_ReactiveResStart.Ability_Smonk_X_PostDeath_ReactiveResStart_C"),
            Equipment("/Game/Characters/Smonk/S0/Ability_X/ReactiveRes/Ability_Smonk_X_ReactiveRes_Engineering.Ability_Smonk_X_ReactiveRes_Engineering_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        SmokeClassNetCacheDescriptors.CreateMovedToPersistentData(SmonkSmokePaths.Ability, 0),
        Rpc("/Game/Characters/Smonk/S0/Ability_4/ReactiveArmor/Ability_Smonk_BFArmor_Reactive.Ability_Smonk_BFArmor_Reactive_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/Ability_Q_Smonk_DebuffKnife.Ability_Q_Smonk_DebuffKnife_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/Projectile_Smonk_DecayNade.Projectile_Smonk_DecayNade_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Smonk/S0/Ability_X/ReactiveRes/Ability_Smonk_X_ReactiveRes_Engineering.Ability_Smonk_X_ReactiveRes_Engineering_C", "MulticastOnItemMovedToPersistentData", 1),
    ];
}
