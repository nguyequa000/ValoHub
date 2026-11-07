using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Sprinter;

public static class SprinterDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new SprinterAgentDescriptor(),
            new NeonTunnelDescriptor(),
            Equipment("/Game/Characters/Sprinter/S0/Ability_4/Ability_Sprinter_4_Tunnel_Production.Ability_Sprinter_4_Tunnel_Production_C"),
            Owned("/Game/Characters/Sprinter/S0/Ability_4/GameObject_Sprinter_4_Tunnel.GameObject_Sprinter_4_Tunnel_C"),
            Equipment("/Game/Characters/Sprinter/S0/Ability_E/Ability_Sprinter_E_Sprint_Production.Ability_Sprinter_E_Sprint_Production_C"),
            Equipment("/Game/Characters/Sprinter/S0/Ability_Q/Ability_Sprinter_Q_GroundStrike_Production.Ability_Sprinter_Q_GroundStrike_Production_C"),
            Owned("/Game/Characters/Sprinter/S0/Ability_Q/GameObject_Sprinter_Q_ElectricSphere.GameObject_Sprinter_Q_ElectricSphere_C"),
            Projectile("/Game/Characters/Sprinter/S0/Ability_Q/Projectile_Sprinter_4_GroundStrike.Projectile_Sprinter_4_GroundStrike_C"),
            Equipment("/Game/Characters/Sprinter/S0/Ability_X/Ability_Sprinter_X_LightningGun_Production.Ability_Sprinter_X_LightningGun_Production_C"),
            Equipment("/Game/Characters/Sprinter/S0/Ability_X/Gun_Sprinter_X_HeavyLightningGun_Production.Gun_Sprinter_X_HeavyLightningGun_Production_C", "OwningPrimaryDataAsset"),
            Equipment("/Game/Characters/Sprinter/S0/Passive/Ability_Sprinter_Passive.Ability_Sprinter_Passive_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        Rpc("/Game/Characters/Sprinter/S0/Ability_4/Ability_Sprinter_4_Tunnel_Production.Ability_Sprinter_4_Tunnel_Production_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sprinter/S0/Ability_4/GameObject_Sprinter_4_Tunnel.GameObject_Sprinter_4_Tunnel_C", "BeginBuildingWall", 0),
        Rpc("/Game/Characters/Sprinter/S0/Ability_E/Ability_Sprinter_E_Sprint_Production.Ability_Sprinter_E_Sprint_Production_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sprinter/S0/Ability_Q/Ability_Sprinter_Q_GroundStrike_Production.Ability_Sprinter_Q_GroundStrike_Production_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sprinter/S0/Ability_Q/Projectile_Sprinter_4_GroundStrike.Projectile_Sprinter_4_GroundStrike_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Sprinter/S0/Ability_X/Ability_Sprinter_X_LightningGun_Production.Ability_Sprinter_X_LightningGun_Production_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sprinter/S0/Ability_X/Gun_Sprinter_X_HeavyLightningGun_Production.Gun_Sprinter_X_HeavyLightningGun_Production_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Sprinter/S0/Passive/Ability_Sprinter_Passive.Ability_Sprinter_Passive_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
