using Replay.Models.Descriptors;
using Replay.Valorant.Reveals.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Hunter;

/// <summary>
/// Sova
/// </summary>
public static class HunterDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new HunterAgentDescriptor(),
            new SovaRevealProjectileDescriptor(),
            new SovaRevealDeviceDescriptor(),
            new SovaRevealPulseDescriptor(),
            Equipment("/Game/Characters/Hunter/S0/Ability_4/Ability_Hunter_4_BoltExplosive.Ability_Hunter_4_BoltExplosive_C"),
            Owned("/Game/Characters/Hunter/S0/Ability_4/GameObject_Hunter_4_ExplosiveBolt_Explosion.GameObject_Hunter_4_ExplosiveBolt_Explosion_C"),
            Projectile("/Game/Characters/Hunter/S0/Ability_4/Projectile_Hunter_4_ExplosiveBolt.Projectile_Hunter_4_ExplosiveBolt_C"),
            Equipment("/Game/Characters/Hunter/S0/Ability_E/Drone/Ability_Hunter_E_DeployDrone.Ability_Hunter_E_DeployDrone_C"),
            Equipment("/Game/Characters/Hunter/S0/Ability_E/Drone/Ability_Hunter_E_Drone_Abilities.Ability_Hunter_E_Drone_Abilities_C"),
            Owned("/Game/Characters/Hunter/S0/Ability_E/Drone/GameObject_Hunter_E_Drone_RevealDart.GameObject_Hunter_E_Drone_RevealDart_C", "Target"),
            Equipment("/Game/Characters/Hunter/S0/Ability_Q/Ability_Hunter_Q_RevealBolt_Signature.Ability_Hunter_Q_RevealBolt_Signature_C"),
            Equipment("/Game/Characters/Hunter/S0/Ability_X/Ability_Hunter_X_LaserMulti.Ability_Hunter_X_LaserMulti_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        Rpc("/Game/Characters/Hunter/S0/Ability_4/Ability_Hunter_4_BoltExplosive.Ability_Hunter_4_BoltExplosive_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Hunter/S0/Ability_4/Projectile_Hunter_4_ExplosiveBolt.Projectile_Hunter_4_ExplosiveBolt_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Hunter/S0/Ability_E/Drone/Ability_Hunter_E_DeployDrone.Ability_Hunter_E_DeployDrone_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Hunter/S0/Ability_Q/Ability_Hunter_Q_RevealBolt_Signature.Ability_Hunter_Q_RevealBolt_Signature_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Hunter/S0/Ability_X/Ability_Hunter_X_LaserMulti.Ability_Hunter_X_LaserMulti_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
