using Replay.Models.Descriptors;
using static Replay.Valorant.Descriptors.Agents.AbilityDescriptorRegistration;

namespace Replay.Valorant.Descriptors.Agents.Sequoia;

public static class SequoiaDescriptors
{
    public const string ArenaCoverPath =
        "/Game/Characters/Sequoia/S0/Ability_X/GameObject_Sequoia_X_ArenaCover.GameObject_Sequoia_X_ArenaCover_C";

    public static List<ExportGroupDescriptor> CreateDescriptors()
    {
        return
        [
            new SequoiaAgentDescriptor(),
            Equipment("/Game/Characters/Sequoia/S0/Ability_4/Ability_Sequoia_4_Wave.Ability_Sequoia_4_Wave_C"),
            Owned("/Game/Characters/Sequoia/S0/Ability_4/GameObject_Sequoia_4_MovingCover.GameObject_Sequoia_4_MovingCover_C"),
            Equipment("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/Ability_Sequoia_E_WreckingBall.Ability_Sequoia_E_WreckingBall_C"),
            Owned("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/GameObject_Sequoia_E_Orb.GameObject_Sequoia_E_Orb_C"),
            Owned("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/GameObject_Sequoia_E_Shield.GameObject_Sequoia_E_Shield_C", "AttachParent", "AttachComponent"),
            Equipment("/Game/Characters/Sequoia/S0/Ability_Q/Ability_Sequoia_Q_FragileMissilePrototypeEquipped.Ability_Sequoia_Q_FragileMissilePrototypeEquipped_C", "Projectile", "WarningActor"),
            Owned("/Game/Characters/Sequoia/S0/Ability_Q/GameObject_Sequoia_Q_FragileMissile_TrajectoryWarning.GameObject_Sequoia_Q_FragileMissile_TrajectoryWarning_C"),
            Projectile("/Game/Characters/Sequoia/S0/Ability_Q/Projectile_Sequoia_Q_FragileMissile.Projectile_Sequoia_Q_FragileMissile_C"),
            Equipment("/Game/Characters/Sequoia/S0/Ability_X/Ability_X_Sequoia_ArenaLineCapture.Ability_X_Sequoia_ArenaLineCapture_C"),
            Actor("/Game/Characters/Sequoia/S0/Ability_X/Actor_Sequoia_X_StandardArena.Actor_Sequoia_X_StandardArena_C", ["Owner", "AssignedPlayspace"]),
            Equipment("/Game/Characters/Sequoia/S0/Ability_X/Equippable_Sequoia_X_ArenaTeleport.Equippable_Sequoia_X_ArenaTeleport_C"),
            Actor(ArenaCoverPath, ["bHidden", "AttachParent", "RelativeScale3D", "AttachComponent"]),
            Owned("/Game/Characters/Sequoia/S0/Ability_X/GameObject_Sequoia_X_LineCapture.GameObject_Sequoia_X_LineCapture_C"),
            new SequoiaTeleportContextDescriptor(),
            Equipment("/Game/Characters/Sequoia/S0/Passive/Ability_Sequoia_Passive.Ability_Sequoia_Passive_C"),
        ];
    }

    public static IReadOnlyList<ClassNetCacheDescriptor> CreateClassNetCacheDescriptors() => MergeClassNetCaches(
    [
        Rpc("/Game/Characters/Sequoia/S0/Ability_4/Ability_Sequoia_4_Wave.Ability_Sequoia_4_Wave_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/Ability_Sequoia_E_WreckingBall.Ability_Sequoia_E_WreckingBall_C", "MulticastCallTimerRefreshedForUlt", 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/Ability_Sequoia_E_WreckingBall.Ability_Sequoia_E_WreckingBall_C", MulticastOnItemMovedToPersistentData, 1),
        Rpc("/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/GameObject_Sequoia_E_Shield.GameObject_Sequoia_E_Shield_C", "MulticastTriggerWidgetShieldBreakAnim", 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_Q/Ability_Sequoia_Q_FragileMissilePrototypeEquipped.Ability_Sequoia_Q_FragileMissilePrototypeEquipped_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_Q/Projectile_Sequoia_Q_FragileMissile.Projectile_Sequoia_Q_FragileMissile_C", "MulticastStopProjectile", 3),
        Rpc("/Game/Characters/Sequoia/S0/Ability_X/Ability_X_Sequoia_ArenaLineCapture.Ability_X_Sequoia_ArenaLineCapture_C", MulticastOnItemMovedToPersistentData, 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_X/Equippable_Sequoia_X_ArenaTeleport.Equippable_Sequoia_X_ArenaTeleport_C", MulticastOnItemMovedToPersistentData, 2),
        Rpc(ArenaCoverPath, "MulticastBeginIntroSequence", 0),
        Rpc("/Game/Characters/Sequoia/S0/Ability_X/GameObject_Sequoia_X_LineCapture.GameObject_Sequoia_X_LineCapture_C", "detonate zone client", 0),
        Rpc("/Game/Characters/Sequoia/S0/Passive/Ability_Sequoia_Passive.Ability_Sequoia_Passive_C", MulticastOnItemMovedToPersistentData, 0),
    ]);
}
