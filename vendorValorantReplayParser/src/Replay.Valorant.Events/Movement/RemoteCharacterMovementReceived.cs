using Replay.Models.Events;
using Replay.Valorant.Movement;

namespace Replay.Valorant.Events.Movement;

public interface IRemoteCharacterMovementSink
{
    void EmitRemoteCharacterMovement(RemoteCharacterMovementReceived movement);
}

public sealed record RemoteCharacterMovementReceived(
    float TimeSeconds,
    int PacketId,
    uint ActorNetGuid,
    uint ObjectNetGuid,
    uint ChannelIndex,
    int UpdateIndex,
    uint ShooterCharacterNetGuidValue,
    int MoveIndex,
    MovementMove Move)
    : ReplayEvent(TimeSeconds, PacketId);
