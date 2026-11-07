using Replay.Models.Descriptors;
using Replay.Valorant.Nearsights.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Vampire;

public static class VampireDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new VampireAgentDescriptor(),
            new ReynaNearsightProjectileDescriptor(),
            new ReynaNearsightSourceDescriptor(),
            Equipment("/Game/Characters/Vampire/S0/Ability_4/Ability_Vampire_4_NearsightAoE.Ability_Vampire_4_NearsightAoE_C"),
            Equipment("/Game/Characters/Vampire/S0/Ability_E/Ability_Vampire_E_Escape.Ability_Vampire_E_Escape_C"),
            Equipment("/Game/Characters/Vampire/S0/Ability_Q/Ability_Vampire_Q_Heal.Ability_Vampire_Q_Heal_C"),
            Owned("/Game/Characters/Vampire/S0/Ability_Q/GameObject_Vampire_Q_Heal_HealPool_AutoActivate.GameObject_Vampire_Q_Heal_HealPool_AutoActivate_C"),
            Owned("/Game/Characters/Vampire/S0/Ability_Q/GameObject_Vampire_Q_Heal_HealPool_High.GameObject_Vampire_Q_Heal_HealPool_High_C"),
            Equipment("/Game/Characters/Vampire/S0/Ability_X/Ability_Vampire_X_Frenzy.Ability_Vampire_X_Frenzy_C"),
            Equipment("/Game/Characters/Vampire/S0/Passive/Ability_Vampire_Passive.Ability_Vampire_Passive_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        NearsightProjectileClassNetCacheDescriptors.CreateStopProjectile(NearsightPaths.ReynaProjectile, 4),
        Rpc("/Game/Characters/Vampire/S0/Ability_4/Ability_Vampire_4_NearsightAoE.Ability_Vampire_4_NearsightAoE_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Vampire/S0/Ability_E/Ability_Vampire_E_Escape.Ability_Vampire_E_Escape_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Vampire/S0/Ability_Q/Ability_Vampire_Q_Heal.Ability_Vampire_Q_Heal_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Vampire/S0/Ability_X/Ability_Vampire_X_Frenzy.Ability_Vampire_X_Frenzy_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Vampire/S0/Passive/Ability_Vampire_Passive.Ability_Vampire_Passive_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
