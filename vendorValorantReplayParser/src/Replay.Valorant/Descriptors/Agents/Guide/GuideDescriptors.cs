using Replay.Models.Descriptors;
using Replay.Valorant.Flashes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Guide;

public static class GuideDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new GuideAgentDescriptor(),
            new SkyeFlashProjectileDescriptor(),
            new SkyeFlashSourceDescriptor(),
            Equipment("/Game/Characters/Guide/S0/Ability_4/Ability_Guide_4_Heal.Ability_Guide_4_Heal_C"),
            Owned("/Game/Characters/Guide/S0/Ability_4/GameObject_Guide_4_Heal_AOE.GameObject_Guide_4_Heal_AOE_C", "AttachParent", "AttachComponent"),
            Equipment("/Game/Characters/Guide/S0/Ability_E/Ability_Guide_E_HawkFlash.Ability_Guide_E_HawkFlash_C"),
            Equipment("/Game/Characters/Guide/S0/Ability_Q/Ability_Guide_Q_PossessableScout.Ability_Guide_Q_PossessableScout_C"),
            Equipment("/Game/Characters/Guide/S0/Ability_Q/Ability_Guide_Q_PossessableScout_ScoutAbilities.Ability_Guide_Q_PossessableScout_ScoutAbilities_C"),
            Equipment("/Game/Characters/Guide/S0/Ability_X/Ability_Guide_X_Pack.Ability_Guide_X_Pack_C"),
            Equipment("/Game/Characters/Guide/S0/Ability_X/Equippable_Guide_X_Pack_Attack.Equippable_Guide_X_Pack_Attack_C"),
            PawnProjectile("/Game/Characters/Guide/S0/Ability_X/Pawn_Guide_X_Pack.Pawn_Guide_X_Pack_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        new SkyeFlashSourceClassNetCacheDescriptor(),
        Rpc("/Game/Characters/Guide/S0/Ability_4/Ability_Guide_4_Heal.Ability_Guide_4_Heal_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Guide/S0/Ability_E/Ability_Guide_E_HawkFlash.Ability_Guide_E_HawkFlash_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Guide/S0/Ability_Q/Ability_Guide_Q_PossessableScout.Ability_Guide_Q_PossessableScout_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Guide/S0/Ability_X/Ability_Guide_X_Pack.Ability_Guide_X_Pack_C", MulticastOnItemMovedToPersistentData, 1),
    ];
}
