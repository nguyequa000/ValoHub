using Replay.Models.Descriptors;
using Replay.Valorant.Flashes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Breach;

public static class BreachDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new BreachAgentDescriptor(),
            new BreachFlashProjectileDescriptor(),
            Equipment("/Game/Characters/Breach/S0/Ability_4/Ability_Breach_4_FusionBlast.Ability_Breach_4_FusionBlast_C"),
            Owned("/Game/Characters/Breach/S0/Ability_4/GameObject_Breach_4_FusionBlast.GameObject_Breach_4_FusionBlast_C"),
            Projectile("/Game/Characters/Breach/S0/Ability_4/Projectile_Breach_4_FusionBlast.Projectile_Breach_4_FusionBlast_C"),
            Equipment("/Game/Characters/Breach/S0/Ability_E/Ability_Breach_E_Fissure.Ability_Breach_E_Fissure_C"),
            new BreachFaultLineDescriptor(),
            Equipment("/Game/Characters/Breach/S0/Ability_Q/Ability_Breach_Q_Flash.Ability_Breach_Q_Flash_C"),
            Equipment("/Game/Characters/Breach/S0/Ability_X/Ability_Breach_X_Shockwave.Ability_Breach_X_Shockwave_C"),
            Owned("/Game/Characters/Breach/S0/Ability_X/GameObject_Breach_X_Shockwave.GameObject_Breach_X_Shockwave_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        new BreachFaultLineClassNetCacheDescriptor(),
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.BreachProjectile),
        Rpc("/Game/Characters/Breach/S0/Ability_4/Ability_Breach_4_FusionBlast.Ability_Breach_4_FusionBlast_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Breach/S0/Ability_4/Projectile_Breach_4_FusionBlast.Projectile_Breach_4_FusionBlast_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Breach/S0/Ability_E/Ability_Breach_E_Fissure.Ability_Breach_E_Fissure_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Breach/S0/Ability_E/GameObject_Breach_E_SweetSpotFissure.GameObject_Breach_E_SweetSpotFissure_C", "DetonateZone", 0),
        Rpc("/Game/Characters/Breach/S0/Ability_Q/Ability_Breach_Q_Flash.Ability_Breach_Q_Flash_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Breach/S0/Ability_X/Ability_Breach_X_Shockwave.Ability_Breach_X_Shockwave_C", MulticastOnItemMovedToPersistentData, 0),
    ]);
}
