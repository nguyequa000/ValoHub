using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.GameState;

public sealed class MulticastResetForRespawnParameters : ExportGroupDescriptor<MulticastResetForRespawnParameters>
{
    public override string Path => "/Script/ShooterGame.AresGameStateBase:MulticastResetForRespawn";
    public override ExportCategory Categories => ExportCategory.GameState;
    public override ExportGroupKind Kind => ExportGroupKind.ClassNetCache;
    public override FieldStreamGrammar Grammar => FieldStreamGrammar.FunctionParameters;

    public NetObjectReference? ShooterCharacter { get; set; }
    public FQuat? Rotation { get; set; }
    public FVector? Translation { get; set; }
    public FVector? Scale3D { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(0, x => x.ShooterCharacter, ExportCategory.GameState)
            .ObjectNetGuid();
        AddPropertyHandle(1, "249", x => x.Rotation).NormalizedQuaternion();
        AddPropertyHandle(2, x => x.Translation).FVector();
        AddPropertyHandle(3, x => x.Scale3D).FVector();
    }
}
