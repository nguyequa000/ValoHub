using Replay.Models.Descriptors;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.Agents.Sequoia;

/// <summary>
/// Iso's arena teleport context. Actor spawn transforms identify the arena and cover placement;
/// this context carries the replicated teleport destination used by the ultimate.
/// </summary>
public sealed class SequoiaTeleportContextDescriptor
    : ExportGroupDescriptor<SequoiaTeleportContextDescriptor>
{
    public const string ExportPath =
        "/Game/Characters/Sequoia/S0/Ability_X/TransitionContext_Sequoia_X_TeleportInfo.TransitionContext_Sequoia_X_TeleportInfo_C";

    public override string Path => ExportPath;
    public override ExportCategory Categories => ExportCategory.Ability | ExportCategory.Movement;

    public FQuat? Rotation { get; set; }
    public FVector? Translation { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(1, "249", x => x.Rotation)
            .NormalizedQuaternion();
        AddPropertyHandle(2, "Translation", x => x.Translation).FVector();
    }
}
