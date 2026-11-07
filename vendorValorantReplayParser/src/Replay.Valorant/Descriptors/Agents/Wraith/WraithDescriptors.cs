using Replay.Models.Descriptors;
using Replay.Valorant.Nearsights.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Wraith;

public static class WraithDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new WraithAgentDescriptor(),
            new DarkCoverAbilityDescriptor(),
            new OmenNearsightProjectileDescriptor(),
            Equipment("/Game/Characters/Wraith/S0/Ability_4/Ability_Wraith_4_Smoke.Ability_Wraith_4_Smoke_C"),
            Projectile("/Game/Characters/Wraith/S0/Ability_4/Projectile_Wraith_4_Smoke.Projectile_Wraith_4_Smoke_C"),
            Equipment("/Game/Characters/Wraith/S0/Ability_E/Ability_Wraith_E_ShortTeleport.Ability_Wraith_E_ShortTeleport_C"),
            Equipment("/Game/Characters/Wraith/S0/Ability_Q/Ability_Wraith_Q_NearsightMissile.Ability_Wraith_Q_NearsightMissile_C", "Projectile", "WarningActor"),
            Owned("/Game/Characters/Wraith/S0/Ability_Q/GameObject_Wraith_Q_NearsightMissile_TrajectoryWarning.GameObject_Wraith_Q_NearsightMissile_TrajectoryWarning_C"),
            Equipment("/Game/Characters/Wraith/S0/Ability_X/Ability_Wraith_X_GlobalTeleport.Ability_Wraith_X_GlobalTeleport_C"),
            Owned("/Game/Characters/Wraith/S0/Ability_X/Intention_Wraith_X_GlobalTeleport.Intention_Wraith_X_GlobalTeleport_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        NearsightProjectileClassNetCacheDescriptors.CreateStopProjectile(NearsightPaths.OmenProjectile, 3),
        Rpc("/Game/Characters/Wraith/S0/Ability_4/Ability_Wraith_4_Smoke.Ability_Wraith_4_Smoke_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wraith/S0/Ability_4/Projectile_Wraith_4_Smoke.Projectile_Wraith_4_Smoke_C", "MulticastStopProjectile", 4),
        Rpc("/Game/Characters/Wraith/S0/Ability_E/Ability_Wraith_E_ShortTeleport.Ability_Wraith_E_ShortTeleport_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wraith/S0/Ability_Q/Ability_Wraith_Q_NearsightMissile.Ability_Wraith_Q_NearsightMissile_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wraith/S0/Ability_X/Ability_Wraith_X_GlobalTeleport.Ability_Wraith_X_GlobalTeleport_C", MulticastOnItemMovedToPersistentData, 1),
    ];
}
