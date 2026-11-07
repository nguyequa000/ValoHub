using Replay.Models.Descriptors;
using Replay.Valorant.Flashes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Stealth;

public static class StealthDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new StealthAgentDescriptor(),
            new YoruFlashProjectileDescriptor(),
            Equipment("/Game/Characters/Stealth/S0/Ability_4/Ability_Stealth_4_Decoy.Ability_Stealth_4_Decoy_C"),
            PawnProjectile("/Game/Characters/Stealth/S0/Ability_4/Pawn_Stealth_4_Decoy_V2.Pawn_Stealth_4_Decoy_V2_C"),
            Equipment("/Game/Characters/Stealth/S0/Ability_E/Ability_Stealth_E_Teleport.Ability_Stealth_E_Teleport_C"),
            PawnProjectile("/Game/Characters/Stealth/S0/Ability_E/Pawn_Stealth_E_TeleporterMoving_FakeTP.Pawn_Stealth_E_TeleporterMoving_FakeTP_C"),
            PawnProjectile("/Game/Characters/Stealth/S0/Ability_E/Pawn_Stealth_E_TeleporterStationary_FakeTP.Pawn_Stealth_E_TeleporterStationary_FakeTP_C"),
            Equipment("/Game/Characters/Stealth/S0/Ability_Q/Ability_Stealth_Q_BounceFlash.Ability_Stealth_Q_BounceFlash_C"),
            Equipment("/Game/Characters/Stealth/S0/Ability_X/Ability_Stealth_X_Cloak_Equip.Ability_Stealth_X_Cloak_Equip_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        FlashProjectileClassNetCacheDescriptors.CreateStopProjectile(FlashPaths.YoruProjectile),
        Rpc("/Game/Characters/Stealth/S0/Ability_4/Ability_Stealth_4_Decoy.Ability_Stealth_4_Decoy_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Stealth/S0/Ability_E/Ability_Stealth_E_Teleport.Ability_Stealth_E_Teleport_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Stealth/S0/Ability_Q/Ability_Stealth_Q_BounceFlash.Ability_Stealth_Q_BounceFlash_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Stealth/S0/Ability_X/Ability_Stealth_X_Cloak_Equip.Ability_Stealth_X_Cloak_Equip_C", MulticastOnItemMovedToPersistentData, 0),
    ];
}
