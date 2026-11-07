using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

public sealed class AresAbilitySystemComponentDescriptor : ExportGroupDescriptor<AresAbilitySystemComponentDescriptor>
{
    public override string Path => "/Script/ShooterGame.AresAbilitySystemComponent";
    public override ExportCategory Categories => ExportCategory.Ability;
    public override ExportGroupKind Kind => ExportGroupKind.Component;

    public NetObjectReference Owner { get; set; }
    public NetObjectReference Instigator { get; set; }
    public uint AresAttributeSet { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty(x => x.Instigator).ObjectNetGuid();
        AddProperty(x => x.AresAttributeSet);
    }
}
