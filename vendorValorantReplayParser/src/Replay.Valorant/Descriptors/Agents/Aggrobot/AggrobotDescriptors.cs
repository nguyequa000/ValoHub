using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Aggrobot;

public static class AggrobotDescriptors
{
    private const string HiddenField = "bHidden";
    private const string PauseExpirationTimer = "Pause Expiration Timer";
    private const string StartShrinking = "Start Shrinking";

    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new AggrobotAgentDescriptor(),
            Equipment("/Game/Characters/AggroBot/S0/Ability_4/Ability_Aggrobot_C_ExplodeyPatch.Ability_Aggrobot_C_ExplodeyPatch_C"),
            Owned("/Game/Characters/AggroBot/S0/Ability_4/Patch_Aggrobot_C_ExplodeyPatch.Patch_Aggrobot_C_ExplodeyPatch_C"),
            Projectile("/Game/Characters/AggroBot/S0/Ability_4/Projectile_Aggrobot_C_ExplodeyPatch.Projectile_Aggrobot_C_ExplodeyPatch_C"),
            Equipment("/Game/Characters/AggroBot/S0/Ability_E/Ability_E_Aggrobot_DiscTurret.Ability_E_Aggrobot_DiscTurret_C"),
            Projectile("/Game/Characters/AggroBot/S0/Ability_E/Projectile_Aggrobot_Zamboni_Rocket.Projectile_Aggrobot_Zamboni_Rocket_C"),
            Projectile("/Game/Characters/AggroBot/S0/Ability_E/Projectile_E_Aggrobot_DiscTurret_PowerWave.Projectile_E_Aggrobot_DiscTurret_PowerWave_C"),
            Projectile("/Game/Characters/AggroBot/S0/Ability_E/Projectile_E_Aggrobot_OrbSpawner.Projectile_E_Aggrobot_OrbSpawner_C"),
            Equipment("/Game/Characters/AggroBot/S0/Ability_Q/Ability_Q_Aggrobot_SeekerNade.Ability_Q_Aggrobot_SeekerNade_C"),
            Equipment("/Game/Characters/AggroBot/S0/Ability_X/Ability_Aggrobot_X_RollyExplosion.Ability_Aggrobot_X_RollyExplosion_C"),
            Equipment("/Game/Characters/AggroBot/S0/Ability_X/Ability_Pawn_Aggrobot_RollyExplodey.Ability_Pawn_Aggrobot_RollyExplodey_C"),
            Equipment("/Game/Characters/AggroBot/S0/Passive/Ability_AggroBot_Passive.Ability_AggroBot_Passive_C"),
            Owned("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch.GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch_C", HiddenField),
            Owned("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_SeekerNade.GameObject_Aggrobot_Reclaim_Orb_SeekerNade_C", HiddenField),
            Owned("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_SeekerNadePlantSuccessful.GameObject_Aggrobot_Reclaim_Orb_SeekerNadePlantSuccessful_C", HiddenField),
            Owned("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_Turret.GameObject_Aggrobot_Reclaim_Orb_Turret_C", HiddenField),
            Owned("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_X_Reclaim_Orb.GameObject_Aggrobot_X_Reclaim_Orb_C", HiddenField),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        Rpc("/Game/Characters/AggroBot/S0/Ability_4/Ability_Aggrobot_C_ExplodeyPatch.Ability_Aggrobot_C_ExplodeyPatch_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/AggroBot/S0/Ability_4/Patch_Aggrobot_C_ExplodeyPatch.Patch_Aggrobot_C_ExplodeyPatch_C", "Do Explosion", 0),
        Rpc("/Game/Characters/AggroBot/S0/Ability_4/Patch_Aggrobot_C_ExplodeyPatch.Patch_Aggrobot_C_ExplodeyPatch_C", "BeginFadeOut", 1),
        Rpc("/Game/Characters/AggroBot/S0/Ability_4/Projectile_Aggrobot_C_ExplodeyPatch.Projectile_Aggrobot_C_ExplodeyPatch_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/AggroBot/S0/Ability_E/Ability_E_Aggrobot_DiscTurret.Ability_E_Aggrobot_DiscTurret_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/AggroBot/S0/Ability_E/Projectile_Aggrobot_Zamboni_Rocket.Projectile_Aggrobot_Zamboni_Rocket_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/AggroBot/S0/Ability_E/Projectile_E_Aggrobot_OrbSpawner.Projectile_E_Aggrobot_OrbSpawner_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/AggroBot/S0/Ability_Q/Ability_Q_Aggrobot_SeekerNade.Ability_Q_Aggrobot_SeekerNade_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/AggroBot/S0/Ability_X/Ability_Aggrobot_X_RollyExplosion.Ability_Aggrobot_X_RollyExplosion_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/AggroBot/S0/Passive/Ability_AggroBot_Passive.Ability_AggroBot_Passive_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch.GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch_C", PauseExpirationTimer, 0),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch.GameObject_Aggrobot_Reclaim_Orb_ExplodeyPatch_C", StartShrinking, 2),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_SeekerNade.GameObject_Aggrobot_Reclaim_Orb_SeekerNade_C", PauseExpirationTimer, 0),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_SeekerNade.GameObject_Aggrobot_Reclaim_Orb_SeekerNade_C", StartShrinking, 2),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_Turret.GameObject_Aggrobot_Reclaim_Orb_Turret_C", PauseExpirationTimer, 0),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_Reclaim_Orb_Turret.GameObject_Aggrobot_Reclaim_Orb_Turret_C", StartShrinking, 2),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_X_Reclaim_Orb.GameObject_Aggrobot_X_Reclaim_Orb_C", PauseExpirationTimer, 0),
        Rpc("/Game/Characters/AggroBot/S0/ReclaimOrbs/GameObject_Aggrobot_X_Reclaim_Orb.GameObject_Aggrobot_X_Reclaim_Orb_C", StartShrinking, 2),
    ]);
}
