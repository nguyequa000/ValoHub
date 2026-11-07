using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Pandemic.SmokeScreen;

public class ProjectileSmokeScreenDescriptor : ExportGroupDescriptor<ProjectileSmokeScreenDescriptor>
{
    public override string Path => "/Game/Characters/Pandemic/S0/Ability_E/Projectile_Pandemic_E_SmokeScreen_NoCollision.Projectile_Pandemic_E_SmokeScreen_NoCollision_C";
    public override ExportCategory Categories => ExportCategory.Ability;
    
    public NetObjectReference Owner { get; set; }
    public FRepMovement ReplicatedMovement { get; set; }
    public NetObjectReference Instigator { get; set; }
    
    protected override void Configure()
    {
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty(x => x.ReplicatedMovement)
            .ReplicatedMovement(ERotatorQuantization.ByteComponents);
        AddProperty(x => x.Instigator).ObjectNetGuid();
    }
}
