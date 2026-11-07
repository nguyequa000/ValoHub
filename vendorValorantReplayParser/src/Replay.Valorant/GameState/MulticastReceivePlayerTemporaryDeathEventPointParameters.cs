using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;
using Replay.Valorant.Combat;

namespace Replay.Valorant.GameState;

public sealed class MulticastReceivePlayerTemporaryDeathEventPointParameters : PointDamageParameters<MulticastReceivePlayerTemporaryDeathEventPointParameters>
{
    public override string Path => "/Script/ShooterGame.ShooterGameState:MulticastReceivePlayerTemporaryDeathEvent_Point";
    public override ExportCategory Categories => ExportCategory.GameState | ExportCategory.Gunplay;
    public NetObjectReference? DamagerPlayer { get; set; }
    public NetObjectReference? DownedPlayer { get; set; }
    public NetObjectReference? TemporaryDeathEquippableUsed { get; set; }
    public bool? RecoversInstantly { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(0, x => x.DamagerPlayer).ObjectNetGuid();
        AddPropertyHandle(1, x => x.DownedPlayer).ObjectNetGuid();
        AddSharedFields(2);
        AddPointFields(2);
        AddPropertyHandle(36, "EquippableUsed", x => x.TemporaryDeathEquippableUsed).ObjectNetGuid();
        AddPropertyHandle(37, "bRecoversInstantly", x => x.RecoversInstantly).Bool();
    }
}
