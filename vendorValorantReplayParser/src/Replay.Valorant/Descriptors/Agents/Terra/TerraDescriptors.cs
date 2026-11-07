using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Terra;

public static class TerraDescriptors
{
    private const string DelayedBeamPath = "/Game/Characters/Terra/S0/Ability_X/GameObject_Terra_X_DelayedBeam_Beam.GameObject_Terra_X_DelayedBeam_Beam_C";

    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new TerraAgentDescriptor(),
            Equipment("/Game/Characters/Terra/S0/Ability_4/Ability_Terra_C_TimeSlowGrenade.Ability_Terra_C_TimeSlowGrenade_C"),
            Projectile("/Game/Characters/Terra/S0/Ability_4/GameObject_Terra_C_TimeSlowGrenade_Explosion.GameObject_Terra_C_TimeSlowGrenade_Explosion_C"),
            Projectile("/Game/Characters/Terra/S0/Ability_4/Projectile_Terra_C_TimeSlowGrenade.Projectile_Terra_C_TimeSlowGrenade_C"),
            Equipment("/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C", "RewindEndPointLocation", "bIsRecording", "bIsRewinding", "RewindPath"),
            Owned("/Game/Characters/Terra/S0/Ability_E/GameObject_Terra_E_RewindTime_RewindTarget.GameObject_Terra_E_RewindTime_RewindTarget_C"),
            Projectile("/Game/Characters/Terra/S0/Ability_E/Projectile_Terra_E_RewindTime_ForObservers.Projectile_Terra_E_RewindTime_ForObservers_C"),
            Equipment("/Game/Characters/Terra/S0/Ability_Q/Ability_Terra_Q_DoubleDash.Ability_Terra_Q_DoubleDash_C"),
            Equipment("/Game/Characters/Terra/S0/Ability_X/Ability_Terra_X_DelayedBeam.Ability_Terra_X_DelayedBeam_C"),
            Owned(DelayedBeamPath),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        Rpc("/Game/Characters/Terra/S0/Ability_4/Ability_Terra_C_TimeSlowGrenade.Ability_Terra_C_TimeSlowGrenade_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Terra/S0/Ability_4/Projectile_Terra_C_TimeSlowGrenade.Projectile_Terra_C_TimeSlowGrenade_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C", "Multicast_OnSuccessfulCast", 0),
        Rpc("/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Terra/S0/Ability_E/Projectile_Terra_E_RewindTime_ForObservers.Projectile_Terra_E_RewindTime_ForObservers_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Terra/S0/Ability_Q/Ability_Terra_Q_DoubleDash.Ability_Terra_Q_DoubleDash_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Terra/S0/Ability_X/Ability_Terra_X_DelayedBeam.Ability_Terra_X_DelayedBeam_C", "HandleRoundReset", 1),
        Rpc("/Game/Characters/Terra/S0/Ability_X/Ability_Terra_X_DelayedBeam.Ability_Terra_X_DelayedBeam_C", MulticastOnItemMovedToPersistentData, 2),
        Rpc(DelayedBeamPath, "MulticastClientDisableMinimapComponent", 0),
        Rpc(DelayedBeamPath, "MulticastClientDisableWarningZone", 1),
        Rpc(DelayedBeamPath, "MulticastClientEnableMinimapComponent", 2),
    ]);
}
