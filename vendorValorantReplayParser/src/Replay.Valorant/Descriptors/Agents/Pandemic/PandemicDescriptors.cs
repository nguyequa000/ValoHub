using Replay.Models.Descriptors;
using Replay.Valorant.Descriptors.Agents.Pandemic.SmokeScreen;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Pandemic;

public static class PandemicDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new PandemicAgentDescriptor(),
            new ViperPitActorDescriptor(),
            new ProjectileSmokeScreenDescriptor(),
            new SmokeScreenManagerDescriptor(),
            .. SnakeBiteDescriptors.CreateDescriptors(),
            Equipment("/Game/Characters/Pandemic/S0/Ability_4/Ability_Pandemic_4_SmokeGrenade.Ability_Pandemic_4_SmokeGrenade_C"),
            Owned("/Game/Characters/Pandemic/S0/Ability_4/GameObject_Pandemic_4_SmokeZone.GameObject_Pandemic_4_SmokeZone_C"),
            Projectile("/Game/Characters/Pandemic/S0/Ability_4/Projectile_Pandemic_4_SmokeGrenade.Projectile_Pandemic_4_SmokeGrenade_C"),
            Equipment("/Game/Characters/Pandemic/S0/Ability_E/Ability_Pandemic_E_SmokeScreen.Ability_Pandemic_E_SmokeScreen_C"),
            Equipment("/Game/Characters/Pandemic/S0/Ability_X/Ability_Pandemic_X_SmokeSite_New.Ability_Pandemic_X_SmokeSite_New_C"),
            Equipment("/Game/Characters/Pandemic/S0/Passive/Ability_Pandemic_Passive.Ability_Pandemic_Passive_C"),
        ];
    }

    public static List<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors()
    {
        return MergeClassNetCaches(
        [
            new SmokeScreenManagerClassNetCacheDescriptor(),
            .. SnakeBiteDescriptors.CreateClassNetCacheDescriptors(),
            Rpc("/Game/Characters/Pandemic/S0/Ability_4/Ability_Pandemic_4_SmokeGrenade.Ability_Pandemic_4_SmokeGrenade_C", MulticastOnItemMovedToPersistentData, 1),
            Rpc("/Game/Characters/Pandemic/S0/Ability_4/GameObject_Pandemic_4_SmokeZone.GameObject_Pandemic_4_SmokeZone_C", "Multicast Destroy Smoke", 0),
            Rpc("/Game/Characters/Pandemic/S0/Ability_4/Projectile_Pandemic_4_SmokeGrenade.Projectile_Pandemic_4_SmokeGrenade_C", "MulticastStopProjectile", 3),
            Rpc("/Game/Characters/Pandemic/S0/Ability_E/Ability_Pandemic_E_SmokeScreen.Ability_Pandemic_E_SmokeScreen_C", MulticastOnItemMovedToPersistentData, 0),
            Rpc("/Game/Characters/Pandemic/S0/Ability_X/Ability_Pandemic_X_SmokeSite_New.Ability_Pandemic_X_SmokeSite_New_C", MulticastOnItemMovedToPersistentData, 0),
            Rpc("/Game/Characters/Pandemic/S0/Passive/Ability_Pandemic_Passive.Ability_Pandemic_Passive_C", MulticastOnItemMovedToPersistentData, 0),
        ]).ToList();
    }
}
