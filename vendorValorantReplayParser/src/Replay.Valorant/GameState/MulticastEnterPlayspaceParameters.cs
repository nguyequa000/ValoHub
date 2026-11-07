using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.GameState;

public sealed class MulticastEnterPlayspaceParameters : ExportGroupDescriptor<MulticastEnterPlayspaceParameters>
{
    public override string Path => "/Script/ShooterGame.ShooterGameState:MulticastEnterPlayspace";
    public override ExportCategory Categories => ExportCategory.GameState;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public NetObjectReference? PlayspaceComponent { get; set; }
    public NetObjectReference? NewPlayspace { get; set; }
    public bool LeaveCurrentPlayspaces { get; set; }
    public bool ExecuteOnOwner { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.PlayspaceComponent, ExportCategory.GameState)
            .ObjectNetGuid();
        AddProperty(x => x.NewPlayspace, ExportCategory.GameState)
            .ObjectNetGuid();
        AddProperty("bLeaveCurrentPlayspaces", x => x.LeaveCurrentPlayspaces, ExportCategory.GameState).Bool();
        AddProperty("bExecuteOnOwner", x => x.ExecuteOnOwner, ExportCategory.GameState).Bool();
    }
}