using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents;

/// <summary>Sparse projectile updates using the recorded movement and ownership handles 10, 11, and 13.</summary>
public abstract class SparseAbilityProjectileDescriptor<TDescriptor> : ExportGroupDescriptor<TDescriptor>
    where TDescriptor : SparseAbilityProjectileDescriptor<TDescriptor>
{
    public override ExportCategory Categories => ExportCategory.Ability | ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;

    public FRepMovement? ReplicatedMovement { get; set; }
    public NetObjectReference? Owner { get; set; }
    public NetObjectReference? Instigator { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(10, x => x.ReplicatedMovement, ExportCategory.Movement)
            .ReplicatedMovement(ERotatorQuantization.ByteComponents);
        AddPropertyHandle(11, x => x.Owner, ExportCategory.Ability).ObjectNetGuid();
        AddPropertyHandle(13, x => x.Instigator, ExportCategory.Ability).ObjectNetGuid();
    }
}
