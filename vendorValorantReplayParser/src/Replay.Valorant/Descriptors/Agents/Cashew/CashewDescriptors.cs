using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Cashew;

public static class CashewDescriptors
{
    private const string AirStrikeMortarPath = "/Game/Characters/Cashew/S0/Ability_E/GameObject_Cashew_E_AirStrikeMortar.GameObject_Cashew_E_AirStrikeMortar_C";
    private const string AirstrikeSegmentPath = "/Game/Characters/Cashew/S0/Ability_X/GameObject_Cashew_X_Segment.GameObject_Cashew_X_Segment_C";

    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new CashewAgentDescriptor(),
            Equipment("/Game/Characters/Cashew/S0/Ability_4/Ability_Cashew_4_Spider_Abilities_SelfDestruct.Ability_Cashew_4_Spider_Abilities_SelfDestruct_C"),
            Equipment("/Game/Characters/Cashew/S0/Ability_4/Ability_Cashew_4_Spider_LockOn.Ability_Cashew_4_Spider_LockOn_C"),
            Owned("/Game/Characters/Cashew/S0/Ability_4/GameObject_Cashew_4_SonarPing.GameObject_Cashew_4_SonarPing_C"),
            PawnProjectile("/Game/Characters/Cashew/S0/Ability_E/AIPawn_Cashew_E_SeekingTargetMissile.AIPawn_Cashew_E_SeekingTargetMissile_C", "EndingStrikeLocation"),
            Equipment("/Game/Characters/Cashew/S0/Ability_E/Ability_Cashew_E_Airstrike.Ability_Cashew_E_Airstrike_C", "Seeking Missile Actor"),
            Projectile(AirStrikeMortarPath),
            Owned("/Game/Characters/Cashew/S0/Ability_E/GameObject_Cashew_E_Explosion.GameObject_Cashew_E_Explosion_C"),
            Owned("/Game/Characters/Cashew/S0/Ability_E/GameObject_Cashew_E_MapMissileMarker.GameObject_Cashew_E_MapMissileMarker_C"),
            Owned("/Game/Characters/Cashew/S0/Ability_E/GameObject_Cashew_E_MapMissileMarker_SecondRocket.GameObject_Cashew_E_MapMissileMarker_SecondRocket_C"),
            Equipment("/Game/Characters/Cashew/S0/Ability_Q/Ability_Cashew_Q_ShellShockGrenade.Ability_Cashew_Q_ShellShockGrenade_C"),
            Owned("/Game/Characters/Cashew/S0/Ability_Q/GameObject_Cashew_Q_ShellShockGrenade.GameObject_Cashew_Q_ShellShockGrenade_C"),
            Projectile("/Game/Characters/Cashew/S0/Ability_Q/Projectile_Cashew_Q_ShellShockGrenade.Projectile_Cashew_Q_ShellShockGrenade_C"),
            Projectile("/Game/Characters/Cashew/S0/Ability_Q/Projectile_Cashew_Q_ShellShockGrenade_Bounce.Projectile_Cashew_Q_ShellShockGrenade_Bounce_C", "AttachParent", "AttachComponent"),
            Equipment("/Game/Characters/Cashew/S0/Ability_X/Ability_Cashew_X_Airstrike.Ability_Cashew_X_Airstrike_C"),
            Owned(AirstrikeSegmentPath),
            Owned("/Game/Characters/Cashew/S0/Ability_X/GameObject_Cashew_X_SegmentManager.GameObject_Cashew_X_SegmentManager_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        Rpc("/Game/Characters/Cashew/S0/Ability_4/Ability_Cashew_4_Spider_LockOn.Ability_Cashew_4_Spider_LockOn_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Cashew/S0/Ability_E/AIPawn_Cashew_E_SeekingTargetMissile.AIPawn_Cashew_E_SeekingTargetMissile_C", "BeginLanding", 0),
        Rpc("/Game/Characters/Cashew/S0/Ability_E/Ability_Cashew_E_Airstrike.Ability_Cashew_E_Airstrike_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc(AirStrikeMortarPath, "MulticastExplosionComplete", 0),
        Rpc(AirStrikeMortarPath, "MulticastOnInitialPreWindupStart", 1),
        Rpc(AirStrikeMortarPath, "MulticastOnPreWindupStart", 2),
        Rpc("/Game/Characters/Cashew/S0/Ability_Q/Ability_Cashew_Q_ShellShockGrenade.Ability_Cashew_Q_ShellShockGrenade_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Cashew/S0/Ability_Q/Projectile_Cashew_Q_ShellShockGrenade.Projectile_Cashew_Q_ShellShockGrenade_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Cashew/S0/Ability_Q/Projectile_Cashew_Q_ShellShockGrenade_Bounce.Projectile_Cashew_Q_ShellShockGrenade_Bounce_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Cashew/S0/Ability_X/Ability_Cashew_X_Airstrike.Ability_Cashew_X_Airstrike_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc(AirstrikeSegmentPath, "Enable Minimap Tell", 0),
        Rpc(AirstrikeSegmentPath, "Finished Activations", 1),
        Rpc(AirstrikeSegmentPath, "Reset Segment", 2),
        Rpc(AirstrikeSegmentPath, "TurnOnVisuals", 3),
    ]);
}
