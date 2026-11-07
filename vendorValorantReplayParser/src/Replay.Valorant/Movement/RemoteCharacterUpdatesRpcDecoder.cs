using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Events;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Movement;

internal sealed class RemoteCharacterUpdatesRpcDecoder : IRpcDecoder
{
    private const int RemoteCharacterUpdatesHandle = 1;
    private const int ShooterCharacterNetGuidValueHandle = 2;
    private const int ComponentDataStreamHandle = 3;
    private const int MaxRemoteCharacterUpdates = 256;

    public static RemoteCharacterUpdatesRpcDecoder Instance { get; } = new();

    private RemoteCharacterUpdatesRpcDecoder()
    {
    }

    public DecodedPayloadResult Decode(ref FieldDecodeContext context, FBitArchive archive)
    {
        var endBit = archive.BitLength;
        if (!TryReadBit(archive, endBit))
        {
            return DecodedPayloadResult.Empty;
        }

        RemoteCharacterUpdateBatch? batch = null;
        bool? fastForwardImportant = null;
        var decodedFieldCount = 0;
        while (archive.BitPosition < endBit)
        {
            var encodedHandle = ReadIntPacked(archive, endBit, nameof(RemoteCharacterUpdatesRpcDecoder));
            if (encodedHandle == 0)
            {
                break;
            }

            var handle = checked((int)encodedHandle - 1);
            var payloadBits = ReadIntPacked(archive, endBit, nameof(RemoteCharacterUpdatesRpcDecoder));
            var payloadEndBit = CheckedPayloadEnd(archive, endBit, payloadBits, nameof(RemoteCharacterUpdatesRpcDecoder));

            if (handle == 0)
            {
                using var flag = archive.ReadSubArchive(checked((int)payloadBits));
                fastForwardImportant = flag.ReadBit();
                flag.EnsureFullyConsumed("bIsReplayFastForwardImportant");
                decodedFieldCount++;
                continue;
            }

            if (handle != RemoteCharacterUpdatesHandle)
            {
                archive.SeekBits(payloadEndBit);
                continue;
            }

            batch = ReadRemoteCharacterUpdates(archive, payloadEndBit);
            archive.SeekBits(payloadEndBit);
            decodedFieldCount++;
        }

        if (batch is null && fastForwardImportant is null) return DecodedPayloadResult.Empty;
        var hasUpdates = batch is not null;
        batch ??= new RemoteCharacterUpdateBatch(0);
        batch.IsReplayFastForwardImportant = fastForwardImportant;
        var diagnostics = CreateDiagnosticFields(context.CaptureDiagnosticFields, fastForwardImportant, hasUpdates, batch);
        return new DecodedPayloadResult(batch, decodedFieldCount, diagnostics);
    }

    private static IReadOnlyList<DecodedReplayField> CreateDiagnosticFields(
        bool captureDiagnosticFields,
        bool? fastForwardImportant,
        bool hasUpdates,
        RemoteCharacterUpdateBatch batch)
    {
        List<DecodedReplayField>? diagnostics = captureDiagnosticFields ? new(2) : null;
        if (diagnostics is not null)
        {
            if (fastForwardImportant is { } important)
                diagnostics.Add(new DecodedReplayField(0, "IsReplayFastForwardImportant", "bIsReplayFastForwardImportant",
                    ExportCategory.Movement, DecodedFieldValue.FromBool(important)));
            if (hasUpdates)
                diagnostics.Add(new DecodedReplayField(1, "RemoteCharacterUpdates", "RemoteCharacterUpdates",
                    ExportCategory.Movement, DecodedFieldValue.FromObject(batch)));
        }
        return diagnostics is null ? Array.Empty<DecodedReplayField>() : diagnostics;
    }

    private static RemoteCharacterUpdateBatch ReadRemoteCharacterUpdates(FBitArchive archive, long endBit)
    {
        var updateCount = ReadIntPacked(archive, endBit, nameof(ReadRemoteCharacterUpdates));
        if (updateCount > MaxRemoteCharacterUpdates)
        {
            throw new ArchiveReadException(
                ArchiveErrorCode.InvalidCount,
                nameof(ReadRemoteCharacterUpdates),
                archive.Position,
                archive.Length,
                updateCount);
        }

        var updates = new RemoteCharacterUpdate?[updateCount];
        while (archive.BitPosition < endBit)
        {
            var encodedIndex = ReadIntPacked(archive, endBit, nameof(ReadRemoteCharacterUpdates));
            if (encodedIndex == 0)
            {
                if (endBit - archive.BitPosition == 8)
                {
                    _ = ReadIntPacked(archive, endBit, nameof(ReadRemoteCharacterUpdates));
                }

                break;
            }

            var index = checked((int)encodedIndex - 1);
            if ((uint)index >= updateCount)
            {
                archive.SeekBits(endBit);
                break;
            }

            updates[index] = ReadRemoteCharacterUpdate(archive, endBit, index);
        }

        var batch = new RemoteCharacterUpdateBatch(checked((int)updateCount));
        foreach (var update in updates)
        {
            if (update is not null)
            {
                batch.AddUpdate(update);
            }
        }

        return batch;
    }

    private static RemoteCharacterUpdate ReadRemoteCharacterUpdate(FBitArchive archive, long endBit, int index)
    {
        var update = new RemoteCharacterUpdate { Index = index };
        while (archive.BitPosition < endBit)
        {
            var encodedHandle = ReadIntPacked(archive, endBit, nameof(ReadRemoteCharacterUpdate));
            if (encodedHandle == 0)
            {
                break;
            }

            var handle = checked((int)encodedHandle - 1);
            var payloadBits = ReadIntPacked(archive, endBit, nameof(ReadRemoteCharacterUpdate));
            var payloadEndBit = CheckedPayloadEnd(archive, endBit, payloadBits, nameof(ReadRemoteCharacterUpdate));

            switch (handle)
            {
                case ShooterCharacterNetGuidValueHandle:
                    if (!TryReadUInt32(archive, payloadEndBit, out var shooterGuid))
                    {
                        throw new ArchiveReadException(
                            ArchiveErrorCode.InvalidBitCount,
                            nameof(ReadRemoteCharacterUpdate),
                            archive.Position,
                            archive.Length,
                            payloadBits);
                    }

                    update.ShooterCharacterNetGuidValue = shooterGuid;
                    break;

                case ComponentDataStreamHandle:
                    update.ComponentDataStream = ComponentDataStream.Decode(archive, payloadEndBit);
                    break;
            }

            archive.SeekBits(payloadEndBit);
        }

        return update;
    }

    private static bool TryReadBit(FBitArchive archive, long endBit)
    {
        return archive.BitPosition < endBit && archive.TryReadBit(out _);
    }

    private static bool TryReadUInt32(FBitArchive archive, long endBit, out uint value)
    {
        if (endBit - archive.BitPosition >= 32)
        {
            value = (uint)archive.ReadBitsToUInt64(32);
            return true;
        }

        value = 0;
        return false;
    }

    private static uint ReadIntPacked(FBitArchive archive, long endBit, string operation) =>
        ComponentDataStream.ReadIntPackedForRpc(archive, endBit, operation);

    private static long CheckedPayloadEnd(FBitArchive archive, long endBit, uint payloadBits, string operation) =>
        ComponentDataStream.CheckedPayloadEndForRpc(archive, endBit, payloadBits, operation);
}
