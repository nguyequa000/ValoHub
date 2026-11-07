using Replay.Models.Descriptors;
using Replay.Models.Unreal;

namespace Replay.Valorant.Descriptors.Agents;

/// <summary>Shared wire registration helpers; ability paths live in each agent's catalog.</summary>
internal static class AbilityDescriptorRegistration
{
    public const string MulticastOnItemMovedToPersistentData = "MulticastOnItemMovedToPersistentData";

    private static readonly string[] EquipmentFields =
    [
        "AttachParent", "RelativeScale3D", "AttachComponent", "Owner", "Instigator",
        "bInPersistentData", "CosmeticRandomSeed", "CreatedByCharacter",
    ];

    public static AbilityActorDescriptor Actor(string path, string[] fields) =>
        ActorWithRotation(path, fields, ERotatorQuantization.ByteComponents);

    /// <summary>Actor whose class path names a pawn; its replicated rotation uses short components.</summary>
    public static AbilityActorDescriptor PawnActor(string path, string[] fields) =>
        ActorWithRotation(path, fields, ERotatorQuantization.ShortComponents);

    public static AbilityActorDescriptor Equipment(string path, params string[] additionalFields) =>
        Actor(path, [.. EquipmentFields, .. additionalFields]);

    public static AbilityActorDescriptor Owned(string path, params string[] additionalFields) =>
        Actor(path, ["Owner", "Instigator", .. additionalFields]);

    public static AbilityActorDescriptor Projectile(string path, params string[] additionalFields) =>
        Owned(path, ["ReplicatedMovement", .. additionalFields]);

    public static AbilityActorDescriptor PawnProjectile(string path, params string[] additionalFields) =>
        PawnActor(path, ["Owner", "Instigator", "ReplicatedMovement", .. additionalFields]);

    public static ClassNetCacheDescriptor Rpc(string path, string name, uint handle) =>
        new(path + "_ClassNetCache", [new RpcDescriptor
        {
            Name = name, FunctionExportPath = path + ":" + name, Handle = handle,
            Categories = ExportCategory.Ability, Decoder = ValorantPayloadDecoders.NoParametersRpc,
        }]);

    public static ClassNetCacheDescriptor ObjectRpc(string path, string name, uint handle, string parameter) =>
        new(path + "_ClassNetCache", [new RpcDescriptor
        {
            Name = name, FunctionExportPath = path + ":" + name, Handle = handle,
            Categories = ExportCategory.Ability, ParameterDescriptor = new AbilityObjectParameters(path + ":" + name, parameter),
        }]);

    /// <summary>Combines cache lists that share a path. Single entries pass through unchanged so their concrete type is kept.</summary>
    public static IReadOnlyList<ClassNetCacheDescriptor> MergeClassNetCaches(IReadOnlyList<ClassNetCacheDescriptor> caches) =>
        caches.GroupBy(c => c.Path, StringComparer.Ordinal)
            .Select(g => g.Count() == 1 ? g.Single() : MergeGroup(g.Key, g))
            .ToArray();

    private static ClassNetCacheDescriptor MergeGroup(string path, IEnumerable<ClassNetCacheDescriptor> caches)
    {
        var functions = caches.SelectMany(c => c.FunctionFields).ToArray();
        var duplicateHandle = functions
            .Where(function => function.Handle.HasValue)
            .GroupBy(function => function.Handle!.Value)
            .FirstOrDefault(group => group.Count() > 1);
        if (duplicateHandle is not null)
        {
            throw new InvalidOperationException(
                $"ClassNetCache '{path}' registers handle {duplicateHandle.Key} more than once.");
        }

        return new ClassNetCacheDescriptor(path, functions);
    }

    private static AbilityActorDescriptor ActorWithRotation(string path, string[] fields, ERotatorQuantization rotation) =>
        new(path, fields.ToHashSet(StringComparer.Ordinal), rotation);
}
