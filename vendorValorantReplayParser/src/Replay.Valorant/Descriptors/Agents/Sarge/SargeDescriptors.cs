using Replay.Models.Descriptors;
using Replay.Valorant.Smokes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Sarge;

public static class SargeDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new SargeAgentDescriptor(),
            new SargeSmokeAbilityDescriptor(),
            new SargeSmokeManagerDescriptor(),
            new SargeSmokeDescriptor(),
            Equipment("/Game/Characters/Sarge/S0/Ability_Molotov/Ability_Sarge_Q_Molotov_Production.Ability_Sarge_Q_Molotov_Production_C"),
            Owned("/Game/Characters/Sarge/S0/Ability_Molotov/Patch_Sarge_Q_Molotov_Production.Patch_Sarge_Q_Molotov_Production_C"),
            Projectile("/Game/Characters/Sarge/S0/Ability_Molotov/Projectile_Sarge_Q_Molotov_Production.Projectile_Sarge_Q_Molotov_Production_C"),
            Equipment("/Game/Characters/Sarge/S0/Ability_OrbitalStrike/Ability_Sarge_X_OrbitalStrike.Ability_Sarge_X_OrbitalStrike_C"),
            Owned("/Game/Characters/Sarge/S0/Ability_OrbitalStrike/GameObject_Sarge_X_OrbitalStrike_Production.GameObject_Sarge_X_OrbitalStrike_Production_C", "Position"),
            Equipment("/Game/Characters/Sarge/S0/Ability_SpeedStim/Ability_Sarge_E_SpeedStim.Ability_Sarge_E_SpeedStim_C"),
            Owned("/Game/Characters/Sarge/S0/Ability_SpeedStim/GameObject_Sarge_E_SpeedStim.GameObject_Sarge_E_SpeedStim_C"),
            Projectile("/Game/Characters/Sarge/S0/Ability_SpeedStim/Projectile_Sarge_E_SpeedStim.Projectile_Sarge_E_SpeedStim_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        SmokeClassNetCacheDescriptors.CreateMovedToPersistentData(SargeSmokePaths.Ability, 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_Molotov/Ability_Sarge_Q_Molotov_Production.Ability_Sarge_Q_Molotov_Production_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_Molotov/Patch_Sarge_Q_Molotov_Production.Patch_Sarge_Q_Molotov_Production_C", "BeginFadeOut", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_Molotov/Projectile_Sarge_Q_Molotov_Production.Projectile_Sarge_Q_Molotov_Production_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Sarge/S0/Ability_OrbitalStrike/Ability_Sarge_X_OrbitalStrike.Ability_Sarge_X_OrbitalStrike_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_OrbitalStrike/GameObject_Sarge_X_OrbitalStrike_Production.GameObject_Sarge_X_OrbitalStrike_Production_C", "BeginLaserFadeOut", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_OrbitalStrike/GameObject_Sarge_X_OrbitalStrike_Production.GameObject_Sarge_X_OrbitalStrike_Production_C", "ReplicateLaserFire", 1),
        Rpc("/Game/Characters/Sarge/S0/Ability_SpeedStim/Ability_Sarge_E_SpeedStim.Ability_Sarge_E_SpeedStim_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_SpeedStim/GameObject_Sarge_E_SpeedStim.GameObject_Sarge_E_SpeedStim_C", "OnActivateDropPod", 0),
        Rpc("/Game/Characters/Sarge/S0/Ability_SpeedStim/Projectile_Sarge_E_SpeedStim.Projectile_Sarge_E_SpeedStim_C", "MulticastStopProjectile", 3),
    ]);
}
