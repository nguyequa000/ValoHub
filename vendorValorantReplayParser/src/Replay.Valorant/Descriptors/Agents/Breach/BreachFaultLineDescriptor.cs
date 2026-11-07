using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Breach;

public sealed class BreachFaultLineDescriptor : ExportGroupDescriptor<BreachFaultLineDescriptor>
{
    public override string Path => BreachFaultLineChargeParameters.ActorPath;
    public override ExportCategory Categories => ExportCategory.Ability | ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;
    public NetObjectReference? Owner { get; set; }
    public NetObjectReference? Instigator { get; set; }
    public FVector? CharacterLocation { get; set; }
    protected override void Configure()
    {
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty(x => x.Instigator).ObjectNetGuid();
        AddProperty(x => x.CharacterLocation).FVector();
    }
}
