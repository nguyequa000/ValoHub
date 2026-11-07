using Replay.Models.Descriptors;
using Replay.Valorant.Smokes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Iris;

public static class IrisDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new IrisAgentDescriptor(),
            new IrisSmokeAbilityDescriptor(),
            new IrisSmokeDescriptor(),
            Equipment("/Game/Characters/Iris/S0/Ability_4/Ability_Iris_Thumper.Ability_Iris_Thumper_C"),
            Projectile("/Game/Characters/Iris/S0/Ability_4/GameObject_Thumper_Concuss.GameObject_Thumper_Concuss_C"),
            Owned("/Game/Characters/Iris/S0/Ability_4/GameObject_Thumper_ConcussPulse.GameObject_Thumper_ConcussPulse_C"),
            Projectile("/Game/Characters/Iris/S0/Ability_4/GameObject_Thumper_Heal.GameObject_Thumper_Heal_C"),
            Projectile("/Game/Characters/Iris/S0/Ability_4/Projectile_Thumper_Concuss.Projectile_Thumper_Concuss_C"),
            Projectile("/Game/Characters/Iris/S0/Ability_4/Projectile_Thumper_Heal.Projectile_Thumper_Heal_C"),
            Equipment("/Game/Characters/Iris/S0/Ability_Q/Ability_Iris_Harmonize.Ability_Iris_Harmonize_C"),
            Equipment("/Game/Characters/Iris/S0/Ability_X/Ability_Iris_X_SonicWave.Ability_Iris_X_SonicWave_C"),
            Owned("/Game/Characters/Iris/S0/Ability_X/GameObject_Iris_X_SonicWave.GameObject_Iris_X_SonicWave_C", "AttachParent", "AttachComponent"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        SmokeClassNetCacheDescriptors.CreateMovedToPersistentData(IrisSmokePaths.Ability, 0),
        Rpc("/Game/Characters/Iris/S0/Ability_4/Ability_Iris_Thumper.Ability_Iris_Thumper_C", "Multicast_UnHideThumperBone", 0),
        Rpc("/Game/Characters/Iris/S0/Ability_4/Ability_Iris_Thumper.Ability_Iris_Thumper_C", "MulticastOnItemMovedToPersistentData", 2),
        Rpc("/Game/Characters/Iris/S0/Ability_4/GameObject_Thumper_Concuss.GameObject_Thumper_Concuss_C", "Lift", 0),
        Rpc("/Game/Characters/Iris/S0/Ability_4/GameObject_Thumper_Heal.GameObject_Thumper_Heal_C", "Lift", 0),
        Rpc("/Game/Characters/Iris/S0/Ability_4/Projectile_Thumper_Concuss.Projectile_Thumper_Concuss_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Iris/S0/Ability_4/Projectile_Thumper_Heal.Projectile_Thumper_Heal_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Iris/S0/Ability_Q/Ability_Iris_Harmonize.Ability_Iris_Harmonize_C", "MulticastOnItemMovedToPersistentData", 0),
        Rpc("/Game/Characters/Iris/S0/Ability_X/Ability_Iris_X_SonicWave.Ability_Iris_X_SonicWave_C", "MulticastOnItemMovedToPersistentData", 1),
        Rpc("/Game/Characters/Iris/S0/Ability_X/GameObject_Iris_X_SonicWave.GameObject_Iris_X_SonicWave_C", "Multicast Timer Expired", 0),
    ]);
}
