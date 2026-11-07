using Replay.Models.Events;
using Replay.Valorant.Movement;

namespace Replay.Valorant.Events.Movement;

internal sealed class ValorantMovementEventEnricher(IReplayEventSink inner) : IReplayEventSink
{
    public void Emit(ReplayEvent replayEvent)
    {
        if (replayEvent is RpcReceived { WasDecoded: true, Payload: RemoteCharacterUpdateBatch batch } rpc)
        {
            foreach (var update in batch.Updates)
            {
                if (update is not { ShooterCharacterNetGuidValue: { } shooterGuid,
                        ComponentDataStream: { HasLatestMove: true } stream })
                {
                    continue;
                }

                inner.Emit(new RemoteCharacterMovementReceived(
                    rpc.TimeSeconds, rpc.PacketId, rpc.ActorNetGuid, rpc.ObjectNetGuid, rpc.ChannelIndex,
                    update.Index, shooterGuid, stream.MoveCount - 1, stream.LatestMove));
            }
        }

        inner.Emit(replayEvent);
    }
}
