using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Pandemic;

public sealed class ViperPitActorDescriptor : ExportGroupDescriptor<ViperPitActorDescriptor>
{
    public const string ExportPath = "/Game/Characters/Pandemic/S0/Ability_X/Patch_Pandemic_X_Circular.Patch_Pandemic_X_Circular_C";
    public override string Path => ExportPath;
    public override ExportCategory Categories => ExportCategory.Ability;
    public NetObjectReference? Owner { get; set; }
    public NetObjectReference? Instigator { get; set; }
    protected override void Configure()
    {
        AddPropertyHandle(11, x => x.Owner).ObjectNetGuid();
        AddPropertyHandle(13, x => x.Instigator).ObjectNetGuid();
    }
}
