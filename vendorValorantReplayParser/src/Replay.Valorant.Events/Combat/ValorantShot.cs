using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Valorant.Combat;

namespace Replay.Valorant.Events.Combat;

public sealed record ValorantShot(
    ulong? EffectId,
    float? StartMovementTime,
    string? SourceId,
    bool? IsLocalEffect,
    bool? IsTransient,
    NetObjectReference? WaitOnReplicationActor,
    EAresAlliance? AllianceFilter,
    FVector? Location,
    FRotator? Rotation,
    float? AmmoRemaining,
    float? NumProjectiles,
    float? RandomSeed,
    float? TracerOption,
    float? BurstShotNumber,
    float? YawSwitch,
    NetObjectReference? FiringPlayerState,
    NetObjectReference? FiringState,
    IReadOnlyList<FVector> AttackVectors,
    NetObjectReference? EffectEquippable,
    ValorantEquippable? Equippable,
    ValorantShotFireMode FireMode = ValorantShotFireMode.Unknown,
    string? FireModeEvidence = null,
    NetObjectReference? EffectContainer = null);

public enum ValorantShotFireMode
{
    Unknown,
    Primary,
    Alternate,
}
