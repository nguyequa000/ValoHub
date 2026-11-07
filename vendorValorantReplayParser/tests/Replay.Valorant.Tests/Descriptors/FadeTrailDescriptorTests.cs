using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors;
using Replay.Valorant.Descriptors.Agents.BountyHunter;

namespace Replay.Valorant.Tests.Descriptors;

public sealed class FadeTrailDescriptorTests
{
    [Test]
    public void CatalogRegistersRecordedTrailActorAndInheritedNativeFunctions()
    {
        var catalog = ValorantDescriptors.CreateCatalog();
        var actor = catalog.ExportGroupDescriptors.Single(d => d.Path == FadeTrailDescriptor.TrailPath);
        Assert.That(actor.Fields.Select(f => f.ExportName), Does.Contain("TrackedActor"));
        Assert.That(actor.Fields.Select(f => f.ExportName), Does.Contain("bIsConsumed"));
        var cache = catalog.ClassNetCacheDescriptors.Single(d => d.Path == FadeTrailDescriptor.TrailPath + "_ClassNetCache");
        Assert.That(cache.FunctionFields.Select(f => (f.Name, f.Handle)), Is.EquivalentTo(new[]
            { ("MulticastAddPoint", (uint?)1), ("MulticastInitializeFromPoints", (uint?)2) }));
        Assert.That(cache.FunctionFields.All(f => f.FunctionExportPath!.StartsWith("/Script/ShooterGame.CustomPath:")), Is.True);
        Assert.That(new FadeTrailAddPointParameters().Grammar, Is.EqualTo(FieldStreamGrammar.FunctionParameters));
        Assert.That(new FadeTrailInitializeParameters().Grammar, Is.EqualTo(FieldStreamGrammar.FunctionParameters));
    }

    [Test]
    public void PointIsNativeUnrealCentimetersAndConsumesExactlyThreeDoubles()
    {
        var value = Decode(new FadeTrailAddPointParameters(), "NewPoint", Vector(100, -200, 300));
        Assert.That((value.VectorValue.X, value.VectorValue.Y, value.VectorValue.Z), Is.EqualTo((100d, -200d, 300d)));
        var zero = Decode(new FadeTrailAddPointParameters(), "NewPoint", Vector(0, 0, 0));
        Assert.That(zero.HasValue, Is.True);
        Assert.That(zero.VectorValue.X, Is.Zero);
        Assert.That(new FadeTrailAddPointParameters().NewPoint, Is.Null);
    }

    [Test]
    public void InitialPointsUseRecordedNestedHandleAndPreserveSparseMissingElements()
    {
        var bytes = ArrayPayload(2, (0, Vector(100, 200, 300)), (1, Vector(400, 500, 600)));
        var points = (FadeTrailInitialPoint[])Decode(new FadeTrailInitializeParameters(), "InitialPoints", bytes).ObjectValue!;
        Assert.That(points, Has.Length.EqualTo(2));
        Assert.That(points.All(p => p.HasDecoded(nameof(p.Position))), Is.True);
        Assert.That(points[1].Position!.Value.X, Is.EqualTo(400));
        var sparse = (FadeTrailInitialPoint[])Decode(new FadeTrailInitializeParameters(), "InitialPoints",
            ArrayPayload(2, (1, Vector(0, 0, 0)))).ObjectValue!;
        Assert.That(sparse[0], Is.Null);
        Assert.That(sparse[1].Position!.Value.X, Is.Zero);
        Assert.That(new FadeTrailInitializeParameters().InitialPoints, Is.Null);
        Assert.That((FadeTrailInitialPoint[])Decode(new FadeTrailInitializeParameters(), "InitialPoints", [0]).ObjectValue!, Is.Empty);
    }

    [Test]
    public void ActorReferencesAndConsumedFalseRemainDifferentFromAbsentFields()
    {
        Assert.That(new FadeTrailDescriptor().TrackedActor, Is.Null);
        Assert.That(new FadeTrailDescriptor().IsConsumed, Is.Null);
        Assert.That(Decode(new FadeTrailDescriptor(), "TrackedActor", [0]).ObjectReferenceValue.NetGuid, Is.Zero);
        Assert.That(Decode(new FadeTrailDescriptor(), "IsConsumed", [0], 1).BoolValue, Is.False);
        Assert.That(Decode(new FadeTrailDescriptor(), "IsConsumed", [1], 1).BoolValue, Is.True);
    }

    [Test]
    public void TruncatedPointsAndArraysAreRejectedWithinTheirBounds()
    {
        var point = Vector(100, 200, 300);
        Assert.Throws<ArchiveReadException>(() => Decode(new FadeTrailAddPointParameters(), "NewPoint", point, 191));
        var array = ArrayPayload(1, (0, point));
        Assert.Throws<ArchiveReadException>(() => Decode(new FadeTrailInitializeParameters(), "InitialPoints", array[..^5]));
    }

    private static byte[] Vector(double x, double y, double z) =>
        [.. BitConverter.GetBytes(x), .. BitConverter.GetBytes(y), .. BitConverter.GetBytes(z)];

    private static byte[] ArrayPayload(int count, params (int Index, byte[] Value)[] elements)
    {
        // Packed array count/index, nested field handle 1, bounded FVector payload, terminators.
        var bytes = new List<byte>(); Packed((uint)count);
        foreach (var (index, value) in elements)
        {
            Packed((uint)index + 1); Packed(2); Packed((uint)value.Length * 8); bytes.AddRange(value); Packed(0);
        }
        Packed(0); return bytes.ToArray();
        void Packed(uint value)
        {
            do { var next = value >> 7; bytes.Add((byte)(((value & 127) << 1) | (next == 0 ? 0u : 1u))); value = next; } while (value != 0);
        }
    }

    private static DecodedFieldValue Decode(ExportGroupDescriptor descriptor, string property, byte[] bytes, int? bits = null)
    {
        using var archive = new BitArchiveReader(bytes, bits ?? bytes.Length * 8);
        var context = new FieldDecodeContext();
        var decoder = (IFieldDecoder)descriptor.Fields.Single(f => f.PropertyName == property).Decoder!;
        var result = decoder.Decode(ref context, archive);
        Assert.That(archive.AtEnd, Is.True);
        return result;
    }
}
