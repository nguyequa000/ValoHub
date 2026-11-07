using Replay.Models.Descriptors;
using Replay.Valorant.Reveals.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.BountyHunter;

public static class BountyHunterDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new BountyHunterAgentDescriptor(),
            new FadeRevealProjectileDescriptor(),
            new FadeRevealDeviceDescriptor(),
            new FadeTrailDescriptor(),
            Equipment("/Game/Characters/BountyHunter/S0/Ability_4/Ability_BountyHunter_4_WolfHoundBendable.Ability_BountyHunter_4_WolfHoundBendable_C"),
            PawnProjectile("/Game/Characters/BountyHunter/S0/Ability_4/Pawn_BountyHunter_4_WolfHound.Pawn_BountyHunter_4_WolfHound_C"),
            Equipment("/Game/Characters/BountyHunter/S0/Ability_E/Ability_E_BountyHunter_ReconDivebomb.Ability_E_BountyHunter_ReconDivebomb_C"),
            Equipment("/Game/Characters/BountyHunter/S0/Ability_Q/Ability_Q_BountyHunter_TetherDivebomb.Ability_Q_BountyHunter_TetherDivebomb_C"),
            Owned("/Game/Characters/BountyHunter/S0/Ability_Q/GameObject_Q_BountyHunter_Tether_SphereExpansion.GameObject_Q_BountyHunter_Tether_SphereExpansion_C"),
            Projectile("/Game/Characters/BountyHunter/S0/Ability_Q/Projectile_Q_BountyHunter_TetherGrenade_SphereExpansion.Projectile_Q_BountyHunter_TetherGrenade_SphereExpansion_C"),
            Equipment("/Game/Characters/BountyHunter/S0/Ability_X/Ability_BountyHunter_X_WaveForm.Ability_BountyHunter_X_WaveForm_C"),
            Owned("/Game/Characters/BountyHunter/S0/Ability_X/GameObject_BountyHunter_X_WaveForm.GameObject_BountyHunter_X_WaveForm_C"),
            Equipment("/Game/Characters/BountyHunter/S0/Passive/Ability_BountyHunter_Passive.Ability_BountyHunter_Passive_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        new FadeTrailClassNetCacheDescriptor(),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_4/Ability_BountyHunter_4_WolfHoundBendable.Ability_BountyHunter_4_WolfHoundBendable_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_E/Ability_E_BountyHunter_ReconDivebomb.Ability_E_BountyHunter_ReconDivebomb_C", MulticastOnItemMovedToPersistentData, 2),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_E/Projectile_E_BountyHunter_Divebomb.Projectile_E_BountyHunter_Divebomb_C", "HideProjectileMesh", 0),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_Q/Ability_Q_BountyHunter_TetherDivebomb.Ability_Q_BountyHunter_TetherDivebomb_C", MulticastOnItemMovedToPersistentData, 2),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_Q/Projectile_Q_BountyHunter_TetherGrenade_SphereExpansion.Projectile_Q_BountyHunter_TetherGrenade_SphereExpansion_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/BountyHunter/S0/Ability_X/Ability_BountyHunter_X_WaveForm.Ability_BountyHunter_X_WaveForm_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/BountyHunter/S0/Passive/Ability_BountyHunter_Passive.Ability_BountyHunter_Passive_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
