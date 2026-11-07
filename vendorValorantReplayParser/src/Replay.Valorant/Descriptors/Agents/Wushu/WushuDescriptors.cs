using Replay.Models.Descriptors;
using Replay.Valorant.Smokes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Wushu;

public static class WushuDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new WushuAgentDescriptor(),
            new WushuSmokeAbilityDescriptor(),
            new WushuSmokeZoneDescriptor(),
            new WushuSmokeProjectileDescriptor(),
            Equipment("/Game/Characters/Wushu/S0/Ability_E/Ability_Wushu_E_Dash.Ability_Wushu_E_Dash_C"),
            Equipment("/Game/Characters/Wushu/S0/Ability_Q/Ability_Wushu_Q_CycloneBoost.Ability_Wushu_Q_CycloneBoost_C"),
            Equipment("/Game/Characters/Wushu/S0/Ability_X/Ability_Wushu_X_Dagger_Production.Ability_Wushu_X_Dagger_Production_C"),
            Equipment("/Game/Characters/Wushu/S0/Glide/Ability_Wushu_Passive_Glide.Ability_Wushu_Passive_Glide_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        SmokeClassNetCacheDescriptors.CreateMovedToPersistentData(WushuSmokePaths.Ability, 1),
        SmokeClassNetCacheDescriptors.CreateStopProjectile(WushuSmokePaths.Projectile, 3),
        Rpc("/Game/Characters/Wushu/S0/Ability_E/Ability_Wushu_E_Dash.Ability_Wushu_E_Dash_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wushu/S0/Ability_Q/Ability_Wushu_Q_CycloneBoost.Ability_Wushu_Q_CycloneBoost_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wushu/S0/Ability_X/Ability_Wushu_X_Dagger_Production.Ability_Wushu_X_Dagger_Production_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Wushu/S0/Glide/Ability_Wushu_Passive_Glide.Ability_Wushu_Passive_Glide_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
