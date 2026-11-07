using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.GameState;

public sealed class EquippableStateMachineComponentDescriptor
    : ExportGroupDescriptor<EquippableStateMachineComponentDescriptor>
{
    public override string Path => "/Script/ShooterGame.EquippableStateMachineComponent";
    public override ExportCategory Categories => ExportCategory.Inventory | ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.Component;

    public NetObjectReference CurrentState { get; set; }
    public NetObjectReference? TransitionContext { get; set; }
    public float AuthStartWorldTime { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.CurrentState).ObjectNetGuid();
        AddProperty(x => x.TransitionContext).ObjectNetGuid();
        AddProperty(x => x.AuthStartWorldTime).Float();
    }
}
