using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Flashes.Descriptors;

// Breach replicates ExitResults as an FHitResult, independently of projectile movement.
internal sealed class BreachFlashHitResultDecoder : IFieldDecoder
{
    public DecodedFieldValue Decode(ref FieldDecodeContext context, FBitArchive archive)
    {
        var flags = archive.ReadByte();
        var time = archive.ReadSingle();
        var location = PrimitiveDecoders.VectorNetQuantize.Decode(ref context, archive).VectorValue;
        var normal = PrimitiveDecoders.VectorNetQuantizeNormal.Decode(ref context, archive).VectorValue;
        var impactPoint = (flags & 4) != 0 ? location
            : PrimitiveDecoders.VectorNetQuantize.Decode(ref context, archive).VectorValue;
        var impactNormal = (flags & 8) != 0 ? normal
            : PrimitiveDecoders.VectorNetQuantizeNormal.Decode(ref context, archive).VectorValue;
        var traceStart = PrimitiveDecoders.VectorNetQuantize.Decode(ref context, archive).VectorValue;
        var traceEnd = PrimitiveDecoders.VectorNetQuantize.Decode(ref context, archive).VectorValue;
        var penetrationDepth = (flags & 64) != 0 ? 0 : archive.ReadSingle();
        var item = (flags & 16) != 0 ? -1 : archive.ReadInt32();
        var physMaterial = archive.ReadIntPacked();
        var actor = archive.ReadIntPacked();
        var manager = archive.ReadIntPacked();
        var instanceIndex = archive.ReadInt32();
        var component = archive.ReadIntPacked();
        var boneName = archive.ReadFName();
        var faceIndex = (flags & 32) != 0 ? -1 : archive.ReadInt32();
        var elementIndex = (flags & 128) != 0 ? (byte)0 : archive.ReadByte();
        return DecodedFieldValue.FromObject(new FHitResult((flags & 1) != 0, (flags & 2) != 0, time,
            location, normal, impactPoint, impactNormal, traceStart, traceEnd, penetrationDepth, item,
            physMaterial, actor, manager, instanceIndex, component, boneName, faceIndex, elementIndex));
    }
}
