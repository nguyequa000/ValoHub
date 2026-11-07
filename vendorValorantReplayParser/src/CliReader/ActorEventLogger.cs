using System.Globalization;
using Microsoft.Extensions.Logging;
using Replay.Models.Descriptors;
using Replay.Models.Events;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Valorant.Combat;
using Replay.Valorant.Events.Combat;

namespace CliReader;

internal sealed class ActorEventLogger : IReplayEventSink
{
    private const int MaxDetailedActorEvents = 4000;
    private const int MaxDecodedValueEvents = 4000;

    private readonly ILogger<ActorEventLogger> _logger;
    private readonly Dictionary<uint, ActorIdentity> _actors = [];
    private readonly HashSet<uint> _loggedActorNetGuids = [];
    private int _detailedActorEventCount;
    private int _decodedValueEventCount;

    public ActorEventLogger(ILogger<ActorEventLogger> logger)
    {
        _logger = logger;
    }

    private int SpawnedCount { get; set; }
    private int ClosedCount { get; set; }
    private int DestroyedCloseCount { get; set; }
    private int ExportGroupCount { get; set; }
    private int SkippedExportGroupCount { get; set; }
    private int RpcCount { get; set; }
    private int DecodedFieldCount { get; set; }

    public void Emit(ReplayEvent replayEvent)
    {
        switch (replayEvent)
        {
            case ActorSpawned spawned:
                SpawnedCount++;
                TrackSpawn(spawned);
                LogSpawn(spawned);
                break;

            case ActorClosed closed:
                ClosedCount++;
                if (closed.Reason == ChannelCloseReason.Destroyed)
                {
                    DestroyedCloseCount++;
                }

                LogClose(closed);
                break;

            case ExportGroupReceived exportGroup:
                ExportGroupCount++;
                if (!exportGroup.WasDecoded)
                {
                    SkippedExportGroupCount++;
                }

                DecodedFieldCount += exportGroup.DecodedFieldCount;
                LogExportGroup(exportGroup);
                break;

            case RpcReceived rpc:
                RpcCount++;
                DecodedFieldCount += rpc.DecodedFieldCount;
                LogRpc(rpc);
                break;

            case ValorantShotReceived shot:
                LogShot(shot);
                break;
        }
    }

    private void LogShot(ValorantShotReceived shot)
    {
        _logger.LogInformation(
            "[{TimeSeconds,8:F3}s] Shot Actor: {ActorNetGuid} Weapon: {Weapon} Rep Actor: {WaitOnReplicationActor} Firing Player State: {FiringPlayerState}\tRemaining ammo: {AmmoRemaining} AttackVector: {AttackVector} Location: {Location} Rotation: {Rotation} Time: {StartMovementTime} YawSwitch: {YawSwitch}",
            shot.TimeSeconds,
            shot.ActorNetGuid,
            shot.Shot.Equippable?.Name ?? "<unknown>",
            shot.Shot.WaitOnReplicationActor,
            shot.Shot.FiringPlayerState,
            shot.Shot.AmmoRemaining,
            shot.Shot.AttackVectors.FirstOrDefault(),
            shot.Shot.Location,
            shot.Shot.Rotation,
            shot.Shot.StartMovementTime,
            shot.Shot.YawSwitch);
    }

    public void LogSummary()
    {
        _logger.LogInformation(
            "Actor events: {SpawnedCount} spawned/opened, {ClosedCount} closed ({DestroyedCloseCount} destroyed)",
            SpawnedCount,
            ClosedCount,
            DestroyedCloseCount);

        _logger.LogInformation(
            "Decoded events: {ExportGroupCount} export groups ({SkippedExportGroupCount} skipped), {RpcCount} RPCs, {DecodedFieldCount} fields",
            ExportGroupCount,
            SkippedExportGroupCount,
            RpcCount,
            DecodedFieldCount);

        var commonArchetypes = _actors.Values
            .Where(actor => !string.IsNullOrWhiteSpace(actor.ArchetypePath))
            .Where(actor => ActorPathInterestFilter.IsInteresting(actor.ArchetypePath!))
            .GroupBy(actor => actor.ArchetypePath!, StringComparer.Ordinal)
            .Select(group => new { Path = group.Key, Count = group.Count() })
            .OrderByDescending(group => group.Count)
            .ThenBy(group => group.Path, StringComparer.Ordinal)
            .Take(25)
            .ToArray();

        if (commonArchetypes.Length > 0)
        {
            _logger.LogInformation(
                "Most common analytics-relevant actor archetypes:{NewLine}{ActorArchetypes}",
                Environment.NewLine,
                string.Join(
                    Environment.NewLine,
                    commonArchetypes.Select(group => $"  {group.Count,5}  {group.Path}")));
        }

        if (_detailedActorEventCount >= MaxDetailedActorEvents)
        {
            _logger.LogInformation(
                "Detailed actor-event output was limited to {MaxDetailedEvents} lines.",
                MaxDetailedActorEvents);
        }

        if (_decodedValueEventCount >= MaxDecodedValueEvents)
        {
            _logger.LogInformation(
                "Decoded value output was limited to {MaxDecodedValueEvents} lines.",
                MaxDecodedValueEvents);
        }
    }

    private void TrackSpawn(ActorSpawned spawned)
    {
        _actors[spawned.ActorNetGuid] = new ActorIdentity(
            spawned.ActorPath,
            spawned.ArchetypePath,
            spawned.TimeSeconds);
    }

    private void LogSpawn(ActorSpawned spawned)
    {
        var path = DisplayPath(spawned.ActorNetGuid);
        if (!ActorPathInterestFilter.IsInteresting(path))
        {
            return;
        }

        if (!TryReserveDetailedActorEvent())
        {
            return;
        }

        _loggedActorNetGuids.Add(spawned.ActorNetGuid);
        _logger.LogInformation(
            "[{TimeSeconds,8:F3}s] Spawn/open actor {ActorNetGuid} on channel {ChannelIndex}: {ActorPath} at {Location}, velocity {Velocity}",
            spawned.TimeSeconds,
            spawned.ActorNetGuid,
            spawned.ChannelIndex,
            path,
            FormatVector(spawned.Location),
            FormatVector(spawned.Velocity));
    }

    private void LogClose(ActorClosed closed)
    {
        if (!_loggedActorNetGuids.Contains(closed.ActorNetGuid))
        {
            return;
        }

        if (!TryReserveDetailedActorEvent())
        {
            return;
        }

        _actors.TryGetValue(closed.ActorNetGuid, out var identity);
        var lifetime = identity?.SpawnTimeSeconds is { } spawnTime
            ? string.Create(CultureInfo.InvariantCulture, $"{closed.TimeSeconds - spawnTime:F3}s")
            : "unknown";

        _logger.LogInformation(
            "[{TimeSeconds,8:F3}s] Close actor {ActorNetGuid} on channel {ChannelIndex}: {ActorPath}, reason {CloseReason}, lifetime {Lifetime}",
            closed.TimeSeconds,
            closed.ActorNetGuid,
            closed.ChannelIndex,
            DisplayPath(closed.ActorNetGuid),
            closed.Reason,
            lifetime);
    }

    private void LogExportGroup(ExportGroupReceived exportGroup)
    {
        if (exportGroup.Payload is null && exportGroup.DiagnosticFields.Count == 0 || !TryReserveDecodedValueEvent())
        {
            return;
        }

        _logger.LogInformation(
            "[{TimeSeconds,8:F3}s] Export {ExportGroupPath} actor {ActorNetGuid} object {ObjectNetGuid}: {Payload}",
            exportGroup.TimeSeconds,
            exportGroup.ExportGroupPath ?? "<unresolved>",
            exportGroup.ActorNetGuid,
            exportGroup.ObjectNetGuid,
            FormatPayload(exportGroup.Payload, exportGroup.DiagnosticFields));
    }

    private void LogRpc(RpcReceived rpc)
    {
        if (!TryReserveDecodedValueEvent())
        {
            return;
        }

        _logger.LogInformation(
            "[{TimeSeconds,8:F3}s] RPC {FunctionName} actor {ActorNetGuid} object {ObjectNetGuid}: {Payload}",
            rpc.TimeSeconds,
            rpc.FunctionName,
            rpc.ActorNetGuid,
            rpc.ObjectNetGuid,
            FormatPayload(rpc.Payload, rpc.DiagnosticFields));
    }

    private bool TryReserveDetailedActorEvent()
    {
        if (_detailedActorEventCount >= MaxDetailedActorEvents)
        {
            return false;
        }

        _detailedActorEventCount++;
        return true;
    }

    private bool TryReserveDecodedValueEvent()
    {
        if (_decodedValueEventCount >= MaxDecodedValueEvents)
        {
            return false;
        }

        _decodedValueEventCount++;
        return true;
    }

    private string DisplayPath(uint actorNetGuid)
    {
        if (!_actors.TryGetValue(actorNetGuid, out var identity))
        {
            return "<unknown>";
        }

        return identity.ArchetypePath ?? identity.ActorPath ?? "<unresolved>";
    }

    private static string FormatFields(IReadOnlyList<DecodedReplayField> fields)
    {
        const int maxFields = 8;
        var values = fields
            .Take(maxFields)
            .Select(field => $"{field.Name ?? field.ExportName ?? $"handle:{field.Handle}"}={FormatValue(field.Value)}");
        var formatted = string.Join(", ", values);
        return fields.Count > maxFields
            ? formatted + string.Create(CultureInfo.InvariantCulture, $", ... +{fields.Count - maxFields}")
            : formatted;
    }

    private static string FormatValue(DecodedFieldValue value) => value.Kind switch
    {
        DecodedFieldValueKind.Bool => value.BoolValue ? "true" : "false",
        DecodedFieldValueKind.Byte => value.ByteValue.ToString("G", CultureInfo.InvariantCulture),
        DecodedFieldValueKind.Int32 => value.Int32Value.ToString("G", CultureInfo.InvariantCulture),
        DecodedFieldValueKind.UInt32 => value.UInt32Value.ToString("G", CultureInfo.InvariantCulture),
        DecodedFieldValueKind.Float => value.FloatValue.ToString("G", CultureInfo.InvariantCulture),
        DecodedFieldValueKind.Double => value.DoubleValue.ToString("G", CultureInfo.InvariantCulture),
        DecodedFieldValueKind.String => value.StringValue ?? string.Empty,
        DecodedFieldValueKind.ObjectReference => string.Create(CultureInfo.InvariantCulture, $"net:{value.ObjectReferenceValue.NetGuid}"),
        DecodedFieldValueKind.Vector => FormatVector(value.VectorValue),
        DecodedFieldValueKind.Rotator => FormatRotator(value.RotatorValue),
        DecodedFieldValueKind.Object => value.ObjectValue?.ToString() ?? "<null>",
        _ => "<none>",
    };

    private static string FormatPayload(object? payload, IReadOnlyList<DecodedReplayField> diagnosticFields)
    {
        if (payload is null)
        {
            return diagnosticFields.Count == 0 ? "<no decoded payload>" : FormatFields(diagnosticFields);
        }

        if (payload is IDecodedPayload decodedPayload)
        {
            var type = payload.GetType();
            var values = decodedPayload.DecodedProperties
                .OrderBy(name => name, StringComparer.Ordinal)
                .Take(8)
                .Select(name => $"{name}={FormatRuntimeValue(type.GetProperty(name)?.GetValue(payload))}")
                .ToArray();

            var formattedValues = values.Length == 0
                ? "<no decoded properties>"
                : string.Join(", ", values);
            var suffix = decodedPayload.DecodedProperties.Count > values.Length
                ? string.Create(CultureInfo.InvariantCulture, $", ... +{decodedPayload.DecodedProperties.Count - values.Length}")
                : string.Empty;
            return $"{type.Name}({formattedValues}{suffix})";
        }

        return payload.ToString() ?? payload.GetType().Name;
    }

    private static string FormatRuntimeValue(object? value) => value switch
    {
        null => "<null>",
        bool boolValue => boolValue ? "true" : "false",
        byte byteValue => byteValue.ToString("G", CultureInfo.InvariantCulture),
        int intValue => intValue.ToString("G", CultureInfo.InvariantCulture),
        uint uintValue => string.Create(CultureInfo.InvariantCulture, $"net:{uintValue}"),
        float floatValue => floatValue.ToString("G", CultureInfo.InvariantCulture),
        double doubleValue => doubleValue.ToString("G", CultureInfo.InvariantCulture),
        string stringValue => stringValue,
        FVector vector => FormatVector(vector),
        FRotator rotator => FormatRotator(rotator),
        byte[] bytes => string.Create(CultureInfo.InvariantCulture, $"byte[{bytes.Length}]"),
        _ => value.ToString() ?? value.GetType().Name,
    };

    private static string FormatVector(FVector? vector) =>
        vector is { } value ? FormatVector(value) : "<unknown>";

    private static string FormatVector(FVector vector) =>
        string.Create(CultureInfo.InvariantCulture, $"({vector.X:F1}, {vector.Y:F1}, {vector.Z:F1})");

    private static string FormatRotator(FRotator rotator) =>
        string.Create(CultureInfo.InvariantCulture, $"({rotator.Pitch:F1}, {rotator.Yaw:F1}, {rotator.Roll:F1})");

    private sealed record ActorIdentity(
        string? ActorPath,
        string? ArchetypePath,
        float? SpawnTimeSeconds);
}
