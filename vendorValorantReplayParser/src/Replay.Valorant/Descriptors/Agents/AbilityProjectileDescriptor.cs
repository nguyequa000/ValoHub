using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents;

/// <summary>Ability projectiles with movement and ownership bound by exported field names.</summary>
public abstract class AbilityProjectileDescriptor<TDescriptor> : ExportGroupDescriptor<TDescriptor>
    where TDescriptor : AbilityProjectileDescriptor<TDescriptor>
{
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;

    public FRepMovement ReplicatedMovement { get; set; }
    public NetObjectReference Owner { get; set; }
    public NetObjectReference Instigator { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.ReplicatedMovement)
            .ReplicatedMovement(ERotatorQuantization.ByteComponents);
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty(x => x.Instigator).ObjectNetGuid();
    }
}
