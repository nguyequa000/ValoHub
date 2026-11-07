using Replay.Models.Descriptors;
using Replay.Valorant.Descriptors.Agents.Mage.TidalWave;
using Replay.Valorant.Nearsights.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Mage;

public static class MageDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new MageAgentDescriptor(),
            Projectile("/Game/Characters/Mage/S0/Ability_E/Projectile_Mage_E_WorldSmoke.Projectile_Mage_E_WorldSmoke_C"),
            new MageWallDescriptor(),
            new CoveAbilityDescriptor(),
            new HarborNearsightProjectileDescriptor(),
            new HarborNearsightSourceDescriptor(),
            .. TidalWaveDescriptors.CreateExportDescriptors(),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        NearsightProjectileClassNetCacheDescriptors.CreateStopProjectile(NearsightPaths.HarborProjectile, 3),
        .. TidalWaveDescriptors.CreateClassNetCacheDescriptors(),
    ];
}
