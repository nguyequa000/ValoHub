using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

public sealed class SmokeAbilityDescriptor : ExportGroupDescriptor<SmokeAbilityDescriptor>
{
    public override string Path => "/Game/Characters/Phoenix/S0/Ability_E/Phoenix_E_Smoke.Phoenix_E_Smoke_C";
    public override ExportCategory Categories => ExportCategory.Ability | ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;

    public NetObjectReference Owner { get; set; }
    public NetObjectReference Instigator { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.Owner, ExportCategory.Ability).ObjectNetGuid();
        AddProperty(x => x.Instigator, ExportCategory.Ability).ObjectNetGuid();
    }
}
