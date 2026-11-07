using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Valorant.Descriptors.Effects.Replay;

namespace Replay.Valorant.Events.Combat;

internal static class ValorantShotFactory
{
    private const string AttackVectorPrefix = "FiringState.AttackVector.";

    public static ValorantShot Create(ReplayPlayContinuousEffectAtLocationParameters parameters)
    {
        return new ValorantShot(
            parameters.EffectId,
            parameters.StartMovementTime,
            parameters.SourceId,
            parameters.IsLocalEffect,
            parameters.IsTransient,
            parameters.WaitOnReplicationActor,
            parameters.AllianceFilter,
            parameters.Location,
            parameters.Rotation,
            GetFloat(parameters, "FiringState.AmmoRemaining"),
            GetFloat(parameters, "FiringState.NumProjectiles"),
            GetFloat(parameters, "FiringState.RandomSeed"),
            GetFloat(parameters, "FiringState.TracerOption"),
            GetFloat(parameters, "FiringState.BurstShotNumber"),
            GetFloat(parameters, "FiringState.YawSwitch"),
            GetObject(parameters, "FiringState.FiringPlayerState"),
            GetObject(parameters, "FiringState.FiringState"),
            GetAttackVectors(parameters),
            GetObject(parameters, "FXC.Equippable"),
            Equippable: null,
            EffectContainer: parameters.EffectContainer);
    }

    private static float? GetFloat(ReplayPlayContinuousEffectAtLocationParameters parameters, string tagName)
    {
        return parameters.FloatValues?.FirstOrDefault(value => value.Name?.TagName == tagName)?.Float;
    }

    private static NetObjectReference? GetObject(ReplayPlayContinuousEffectAtLocationParameters parameters, string tagName)
    {
        return parameters.ObjectValues?.FirstOrDefault(value => value.Name?.TagName == tagName)?.Object;
    }

    private static IReadOnlyList<FVector> GetAttackVectors(ReplayPlayContinuousEffectAtLocationParameters parameters)
    {
        if (parameters.VectorValues is null)
        {
            return [];
        }

        return parameters.VectorValues
            .Select(value => new
            {
                Index = GetAttackVectorIndex(value.Name?.TagName),
                value.Vector,
            })
            .Where(value => value.Index is >= 1 and <= 15 && value.Vector.HasValue)
            .OrderBy(value => value.Index)
            .Select(value => value.Vector!.Value)
            .ToArray();
    }

    private static int? GetAttackVectorIndex(string? tagName)
    {
        if (tagName is null || !tagName.StartsWith(AttackVectorPrefix, StringComparison.Ordinal))
        {
            return null;
        }

        return int.TryParse(tagName[AttackVectorPrefix.Length..], out var index) ? index : null;
    }
}
