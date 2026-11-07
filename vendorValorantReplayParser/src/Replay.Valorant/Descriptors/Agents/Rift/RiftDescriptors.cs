using Replay.Models.Descriptors;
using Replay.Valorant.Smokes.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Rift;

public static class RiftDescriptors
{
    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new RiftAgentDescriptor(),
            new RiftSmokeAbilityDescriptor(),
            new RiftWorldTargetingSmokeAbilityDescriptor(),
            new RiftSmokeZoneDescriptor(),
            new RiftFakeSmokeZoneDescriptor(),
            Equipment("/Game/Characters/Rift/S0/Ability_4/BlackHole/Ability_Rift_4_BlackHole.Ability_Rift_4_BlackHole_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_4/BlackHole/Ability_Rift_4_BlackHole_WorldTargeting.Ability_Rift_4_BlackHole_WorldTargeting_C"),
            Owned("/Game/Characters/Rift/S0/Ability_4/BlackHole/GameObject_Rift_4_BlackHole.GameObject_Rift_4_BlackHole_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_Passive/Ability_Rift_Passive_RiftCharges.Ability_Rift_Passive_RiftCharges_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_Q/Ability_Rift_Q_FlashBurst.Ability_Rift_Q_FlashBurst_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_Q/Ability_Rift_Q_FlashBurst_WorldTargeting.Ability_Rift_Q_FlashBurst_WorldTargeting_C"),
            Owned("/Game/Characters/Rift/S0/Ability_Q/GameObject_Rift_Q_FlashBurst.GameObject_Rift_Q_FlashBurst_C", "bHidden"),
            Equipment("/Game/Characters/Rift/S0/Ability_X/Ability_Rift_X_WorldTargetingStart.Ability_Rift_X_WorldTargetingStart_C"),
            Owned("/Game/Characters/Rift/S0/Ability_X/GameObject_Rift_X_Markers.GameObject_Rift_X_Markers_C", "bHidden", "IsOwnerAlive", "Is Placed In World"),
            Equipment("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/Ability_Rift_X_WorldTargeting_EquipSlotOnSpawnedCharacter_Melee.Ability_Rift_X_WorldTargeting_EquipSlotOnSpawnedCharacter_Melee_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/Ability_Rift_X_WorldTargeting_EquipSlotOnSpawnedCharacter_Secondary.Ability_Rift_X_WorldTargeting_EquipSlotOnSpawnedCharacter_Secondary_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/Ability_Rift_X_WorldTargeting_Unpossess.Ability_Rift_X_WorldTargeting_Unpossess_C"),
            Equipment("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/GlobalWall/Ability_Rift_X_PlaceMarkers_WorldTargeting_GlobalWall.Ability_Rift_X_PlaceMarkers_WorldTargeting_GlobalWall_C"),
            Owned("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/GlobalWall/GameObject_Rift_X_GlobalWall.GameObject_Rift_X_GlobalWall_C"),
            Projectile("/Game/Characters/Rift/S0/Ability_X/WorldTargeting/Rift_TargetingForm_PC.Rift_TargetingForm_PC_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() =>
    [
        SmokeClassNetCacheDescriptors.CreateMovedToPersistentData(RiftSmokePaths.Ability, 0),
        Rpc("/Game/Characters/Rift/S0/Ability_4/BlackHole/Ability_Rift_4_BlackHole.Ability_Rift_4_BlackHole_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Rift/S0/Ability_Passive/Ability_Rift_Passive_RiftCharges.Ability_Rift_Passive_RiftCharges_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Rift/S0/Ability_Q/Ability_Rift_Q_FlashBurst.Ability_Rift_Q_FlashBurst_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Rift/S0/Ability_X/Ability_Rift_X_WorldTargetingStart.Ability_Rift_X_WorldTargetingStart_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Rift/S0/Ability_X/GameObject_Rift_X_Markers.GameObject_Rift_X_Markers_C", "MulticastDeactivateRift", 0),
    ];
}
