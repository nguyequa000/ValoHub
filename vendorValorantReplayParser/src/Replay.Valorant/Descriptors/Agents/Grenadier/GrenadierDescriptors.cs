using Replay.Models.Descriptors;
using Replay.Valorant.Flashes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Grenadier;

public static class GrenadierDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new GrenadierAgentDescriptor(),
            Equipment("/Game/Characters/Grenadier/S0/Ability_E/Ability_E_Grenadier_EMPKnife.Ability_E_Grenadier_EMPKnife_C"),
            Projectile("/Game/Characters/Grenadier/S0/Ability_E/Projectile_Grenadier_E_SuppressionBlade.Projectile_Grenadier_E_SuppressionBlade_C"),
            Projectile("/Game/Characters/Grenadier/S0/Ability_E/Gameobject_Grenadier_E_SuppressionPulse.Gameobject_Grenadier_E_SuppressionPulse_C"),
            Equipment("/Game/Characters/Grenadier/S0/Ability_Q/Ability_Grenadier_Q_BasicSemtex.Ability_Grenadier_Q_BasicSemtex_C"),
            Projectile("/Game/Characters/Grenadier/S0/Ability_Q/Projectile_Grenadier_Q_SemtexBasic.Projectile_Grenadier_Q_SemtexBasic_C"),
            Equipment("/Game/Characters/Grenadier/S0/Ability_X/Ability_Grenadier_X_OverloadPulse.Ability_Grenadier_X_OverloadPulse_C"),
            Owned("/Game/Characters/Grenadier/S0/Ability_X/Gameobject_Grenadier_X_UltPulse.Gameobject_Grenadier_X_UltPulse_C"),
            new KayoOverhandFlashProjectileDescriptor(),
            new KayoUnderhandFlashProjectileDescriptor(),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.KayoOverhandProjectile),
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.KayoUnderhandProjectile),
    ];
}
