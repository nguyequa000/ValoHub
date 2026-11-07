using Replay.Models.Events;
using Replay.Valorant.Combat;
using Replay.Valorant.Events.Combat;
using Replay.Valorant.Events.Flashes;
using Replay.Valorant.Events.Movement;
using Replay.Valorant.Events.Nearsights;
using Replay.Valorant.Events.Footsteps;
using Replay.Valorant.Events.Walls;
using Replay.Valorant.GameState;
using Replay.Valorant.Movement;

namespace CliReader.JsonExport;

internal sealed class ReplayExportSink :
    IReplayEventSink,
    IRemoteCharacterMovementSink,
    IDisposable
{
    private readonly NdjsonWriter _events;
    private readonly NdjsonWriter _movement;
    private readonly ReplayEventJsonWriter _eventWriter;
    private readonly HashSet<int> _emittedRoundOutcomes = [];
    private readonly Dictionary<uint, string> _actorClassPaths = [];
    private int _redScore;
    private int _blueScore;

    internal ReplayExportSink(
        NdjsonWriter events,
        NdjsonWriter movement,
        ReplayEventJsonWriter eventWriter)
    {
        _events = events;
        _movement = movement;
        _eventWriter = eventWriter;
    }

    public ReplayExportStatistics Statistics { get; } = new();

    public static ReplayExportSink Create(string outputDirectory)
    {
        Directory.CreateDirectory(outputDirectory);
        var events = NdjsonWriter.Create(Path.Combine(outputDirectory, "events.ndjson"));
        try
        {
            var movement = NdjsonWriter.Create(Path.Combine(outputDirectory, "movement.ndjson"));
            return new ReplayExportSink(
                events,
                movement,
                new ReplayEventJsonWriter(new ReplayJsonNormalizer()));
        }
        catch
        {
            events.Dispose();
            throw;
        }
    }

    public void Emit(ReplayEvent replayEvent)
    {
        switch (replayEvent)
        {
            case ActorSpawned spawned:
                if (spawned.ReplicationClassPath is { } classPath)
                {
                    _actorClassPaths[spawned.ActorNetGuid] = classPath;
                }
                _events.Write(writer => _eventWriter.WriteActorSpawned(writer, spawned));
                Statistics.ActorSpawnedCount++;
                break;
            case ActorClosed closed:
                _events.Write(writer => ReplayEventJsonWriter.WriteActorClosed(writer, closed));
                Statistics.ActorClosedCount++;
                break;
            case ExportGroupReceived exportGroup:
                EmitExportGroup(exportGroup);
                break;
            case RpcReceived rpc:
                _events.Write(writer => _eventWriter.WriteRpc(writer, rpc));
                Statistics.RpcCount++;
                EmitPlayerLifeChanges(rpc);
                break;
            case ValorantShotReceived shot:
                _events.Write(writer => _eventWriter.WriteValorantShot(writer, shot));
                Statistics.ValorantShotReceivedCount++;
                break;
            case ValorantFootstepReceived footstep:
                _events.Write(writer => _eventWriter.WriteValorantFootstep(writer, footstep));
                Statistics.ValorantFootstepReceivedCount++;
                break;
            case ValorantFlashCast cast:
                _events.Write(writer => _eventWriter.WriteValorantFlashCast(writer, cast));
                Statistics.ValorantFlashCastCount++;
                break;
            case ValorantFlashPathUpdated path:
                _events.Write(writer => _eventWriter.WriteValorantFlashPathUpdated(writer, path));
                Statistics.ValorantFlashPathUpdatedCount++;
                break;
            case ValorantFlashExploded exploded:
                _events.Write(writer => _eventWriter.WriteValorantFlashExploded(writer, exploded));
                Statistics.ValorantFlashExplodedCount++;
                break;
            case ValorantFlashPlayerHit hit:
                _events.Write(writer => _eventWriter.WriteValorantFlashPlayerHit(writer, hit));
                Statistics.ValorantFlashPlayerHitCount++;
                break;
            case ValorantNearsightCast cast:
                _events.Write(writer => _eventWriter.WriteValorantNearsightCast(writer, cast));
                Statistics.ValorantNearsightCastCount++;
                break;
            case ValorantNearsightPathUpdated path:
                _events.Write(writer => _eventWriter.WriteValorantNearsightPathUpdated(writer, path));
                Statistics.ValorantNearsightPathUpdatedCount++;
                break;
            case ValorantNearsightActivated activated:
                _events.Write(writer => _eventWriter.WriteValorantNearsightActivated(writer, activated));
                Statistics.ValorantNearsightActivatedCount++;
                break;
            case ValorantNearsightPlayerHit hit:
                _events.Write(writer => _eventWriter.WriteValorantNearsightPlayerHit(writer, hit));
                Statistics.ValorantNearsightPlayerHitCount++;
                break;
            case ValorantNearsightPlayerEffectEnded ended:
                _events.Write(writer => _eventWriter.WriteValorantNearsightPlayerEffectEnded(writer, ended));
                Statistics.ValorantNearsightPlayerEffectEndedCount++;
                break;
            case ValorantWallPlaced placed:
                _events.Write(writer => _eventWriter.WriteValorantWallPlaced(writer, placed));
                Statistics.ValorantWallPlacedCount++;
                break;
            case ValorantWallSegmentSpawned spawned:
                _events.Write(writer => _eventWriter.WriteValorantWallSegmentSpawned(writer, spawned));
                Statistics.ValorantWallSegmentSpawnedCount++;
                break;
            case ValorantWallActivated activated:
                _events.Write(writer => _eventWriter.WriteValorantWallActivated(writer, activated));
                Statistics.ValorantWallActivatedCount++;
                break;
            case ValorantWallSegmentDestroyed destroyed:
                _events.Write(writer => _eventWriter.WriteValorantWallSegmentDestroyed(writer, destroyed));
                Statistics.ValorantWallSegmentDestroyedCount++;
                break;
            case ValorantWallDestroyed destroyed:
                _events.Write(writer => _eventWriter.WriteValorantWallDestroyed(writer, destroyed));
                Statistics.ValorantWallDestroyedCount++;
                break;
            case RemoteCharacterMovementReceived movement:
                EmitRemoteCharacterMovement(movement);
                break;
        }
    }

    public void EmitRemoteCharacterMovement(RemoteCharacterMovementReceived movement)
    {
        _movement.Write(writer => _eventWriter.WriteMovement(writer, movement));
        Statistics.MovementCount++;
    }

    public void Dispose()
    {
        try
        {
            _events.Dispose();
        }
        finally
        {
            _movement.Dispose();
        }
    }

    private void EmitExportGroup(ExportGroupReceived exportGroup)
    {
        if (!exportGroup.WasDecoded || exportGroup.Payload is null)
        {
            Statistics.RecordFilteredExportGroup(exportGroup);
            return;
        }

        _events.Write(writer => _eventWriter.WriteExportGroup(writer, exportGroup));
        Statistics.ExportGroupCount++;
        EmitRoundOutcomes(exportGroup);
    }

    private void EmitRoundOutcomes(ExportGroupReceived exportGroup)
    {
        if (exportGroup.Payload is not BombGameStateDescriptor
            {
                RoundResults: AresRoundResultsUpdate { Updates: var results },
            })
        {
            return;
        }

        foreach (var result in results)
        {
            if (result.WinningTeam is null || result.WinningTeamRole is null || result.RoundResult is null ||
                !_emittedRoundOutcomes.Add(result.RoundNumber))
            {
                continue;
            }

            if (string.Equals(result.WinningTeam, "Red", StringComparison.OrdinalIgnoreCase)) _redScore++;
            else if (string.Equals(result.WinningTeam, "Blue", StringComparison.OrdinalIgnoreCase)) _blueScore++;

            _events.Write(writer => ReplayEventJsonWriter.WriteRoundOutcome(
                writer,
                exportGroup,
                result,
                _redScore,
                _blueScore));
            Statistics.RoundOutcomeCount++;
        }
    }

    private void EmitPlayerLifeChanges(RpcReceived rpc)
    {
        if (!_actorClassPaths.TryGetValue(rpc.ActorNetGuid, out var characterClassPath) ||
            !characterClassPath.EndsWith("_PC", StringComparison.Ordinal))
        {
            return;
        }

        switch (rpc.Payload)
        {
            case MulticastNotifyDamageBaseParameters damage:
                EmitDamageLifeChanges(rpc, characterClassPath, damage.LifeChangeEvents);
                break;
            case MulticastNotifyDamagePointParameters damage:
                EmitDamageLifeChanges(rpc, characterClassPath, damage.LifeChangeEvents);
                break;
            case MulticastNotifyHealParameters heal:
                EmitTypedLifeChanges(rpc, characterClassPath, "heal", heal.LifeChanges);
                break;
            case MulticastNotifyOverhealDecayParameters decay:
                EmitTypedLifeChanges(rpc, characterClassPath, "overheal_decay", decay.LifeChanges);
                break;
            case MulticastSectionLifeChangeParameters section:
                EmitTypedLifeChanges(rpc, characterClassPath, "section", section.LifeChanges);
                break;
        }
    }

    private void EmitDamageLifeChanges(
        RpcReceived rpc,
        string characterClassPath,
        IEnumerable<DamageLifeChangeEvent?>? changes)
    {
        if (changes is null) return;
        foreach (var change in changes)
        {
            if (change is null) continue;
            EmitPlayerLifeChange(
                rpc,
                characterClassPath,
                "damage",
                change.ChangedComponent.NetGuid,
                change.LifeResult,
                change.DeltaLife,
                change.AliveAfterChange);
        }
    }

    private void EmitTypedLifeChanges(
        RpcReceived rpc,
        string characterClassPath,
        string changeKind,
        IEnumerable<HealLifeChangeEvent?>? changes)
    {
        if (changes is null) return;
        foreach (var change in changes)
        {
            if (change is null) continue;
            EmitPlayerLifeChange(
                rpc,
                characterClassPath,
                changeKind,
                change.ChangedComponent.NetGuid,
                change.LifeResult,
                change.DeltaLife,
                change.AliveAfterChange);
        }
    }

    private void EmitTypedLifeChanges(
        RpcReceived rpc,
        string characterClassPath,
        string changeKind,
        IEnumerable<SectionLifeChangeEvent?>? changes)
    {
        if (changes is null) return;
        foreach (var change in changes)
        {
            if (change is null) continue;
            EmitPlayerLifeChange(
                rpc,
                characterClassPath,
                changeKind,
                change.ChangedComponent.NetGuid,
                change.LifeResult,
                change.DeltaLife,
                change.AliveAfterChange);
        }
    }

    private void EmitPlayerLifeChange(
        RpcReceived rpc,
        string characterClassPath,
        string changeKind,
        uint changedComponentNetGuid,
        float lifeResult,
        float deltaLife,
        bool aliveAfterChange)
    {
        _events.Write(writer => ReplayEventJsonWriter.WritePlayerLifeChange(
            writer,
            rpc,
            changeKind,
            characterClassPath,
            changedComponentNetGuid,
            lifeResult,
            deltaLife,
            aliveAfterChange));
        Statistics.PlayerLifeChangeCount++;
    }
}
