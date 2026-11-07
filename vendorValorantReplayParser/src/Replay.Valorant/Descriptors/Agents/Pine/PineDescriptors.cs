using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Pine;

public static class PineDescriptors
{
    private const string UsableTeleportPath = "/Game/Characters/Pine/S0/Ability_4/GameObject_Pine_4_UsableTeleport.GameObject_Pine_4_UsableTeleport_C";

    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new PineAgentDescriptor(),
            Equipment("/Game/Characters/Pine/S0/Ability_4/Ability_Pine_4_UsableTP.Ability_Pine_4_UsableTP_C", "Current Usable Target"),
            Owned(UsableTeleportPath, "bHidden"),
            Equipment("/Game/Characters/Pine/S0/Ability_E/Ability_Pine_E_RadEater.Ability_Pine_E_RadEater_C", "Spawned Deployable"),
            PawnProjectile("/Game/Characters/Pine/S0/Ability_E/Pawn_Pine_E_RadEater.Pawn_Pine_E_RadEater_C"),
            Projectile("/Game/Characters/Pine/S0/Ability_E/Projectile_Pine_E_RadEaterSpawner.Projectile_Pine_E_RadEaterSpawner_C"),
            Equipment("/Game/Characters/Pine/S0/Ability_Q/Ability_Pine_Q_SeizeTrap.Ability_Pine_Q_SeizeTrap_C"),
            Owned("/Game/Characters/Pine/S0/Ability_Q/GameObject_Pine_Q_SeizeTrap.GameObject_Pine_Q_SeizeTrap_C"),
            Owned("/Game/Characters/Pine/S0/Ability_Q/GameObject_Pine_Q_Tether_SphereExpansion.GameObject_Pine_Q_Tether_SphereExpansion_C"),
            Projectile("/Game/Characters/Pine/S0/Ability_Q/Projectile_Pine_Q_TrapGrenade.Projectile_Pine_Q_TrapGrenade_C"),
            Equipment("/Game/Characters/Pine/S0/Ability_X/Ability_Pine_X_SelfBuffVers_Engineering.Ability_Pine_X_SelfBuffVers_Engineering_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        Rpc("/Game/Characters/Pine/S0/Ability_4/Ability_Pine_4_UsableTP.Ability_Pine_4_UsableTP_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc(UsableTeleportPath, "EventPineConeIntroAnimation", 0),
        Rpc(UsableTeleportPath, "MultiCastEventPineConeIntroAnimation", 2),
        Rpc(UsableTeleportPath, "MulticastFinishDeploy", 3),
        Rpc("/Game/Characters/Pine/S0/Ability_E/Ability_Pine_E_RadEater.Ability_Pine_E_RadEater_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Pine/S0/Ability_E/Pawn_Pine_E_RadEater.Pawn_Pine_E_RadEater_C", "MultiCast_Activated", 0),
        Rpc("/Game/Characters/Pine/S0/Ability_E/Projectile_Pine_E_RadEaterSpawner.Projectile_Pine_E_RadEaterSpawner_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Pine/S0/Ability_Q/Ability_Pine_Q_SeizeTrap.Ability_Pine_Q_SeizeTrap_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Pine/S0/Ability_Q/GameObject_Pine_Q_SeizeTrap.GameObject_Pine_Q_SeizeTrap_C", "MulticastDeployTrap", 0),
        Rpc("/Game/Characters/Pine/S0/Ability_Q/Projectile_Pine_Q_TrapGrenade.Projectile_Pine_Q_TrapGrenade_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Pine/S0/Ability_X/Ability_Pine_X_SelfBuffVers_Engineering.Ability_Pine_X_SelfBuffVers_Engineering_C", MulticastOnItemMovedToPersistentData, 0),
    ]);
}
