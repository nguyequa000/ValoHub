using Replay.Models.Descriptors;
using Replay.Valorant.Flashes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Phoenix;

public static class PhoenixDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new PhoenixAgentDescriptor(),
            Equipment("/Game/Characters/Phoenix/S0/Ability_4/Production/Ability_Phoenix_4_Molotov_Production.Ability_Phoenix_4_Molotov_Production_C"),
            Projectile("/Game/Characters/Phoenix/S0/Ability_4/Production/Projectile_Phoenix_4_Molotov_Production.Projectile_Phoenix_4_Molotov_Production_C"),
            new FlameWallDescriptor(),
            new PhoenixLeftFlashProjectileDescriptor(),
            new PhoenixRightFlashProjectileDescriptor(),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.PhoenixLeftProjectile),
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.PhoenixRightProjectile),
    ];
}
