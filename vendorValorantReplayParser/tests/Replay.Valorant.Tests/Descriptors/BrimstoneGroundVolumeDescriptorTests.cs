using Replay.Encoding.Archives;
using Replay.Models.Errors;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors;
using Replay.Valorant.Descriptors.DynamicVolume;

namespace Replay.Valorant.Tests.Descriptors;

public sealed class BrimstoneGroundVolumeDescriptorTests
{
    [TestCase(0, 4529, 0, 2)]
    [TestCase(1, 11181, 2, 5)]
    public void RecordedFragmentInfoConsumesBoundedPayload(int index, int bits, int baseKey, int count)
    {
        using var archive = new BitArchiveReader(File.ReadAllBytes(Fixture(index)).AsMemory(), bits);
        var context = new FieldDecodeContext();
        var result = new GroundVolumeFragmentInfoDecoder().Decode(ref context, archive);
        var volume = (GroundVolumeComponentDescriptor)result.Payload!;
        Assert.Multiple(() =>
        {
            Assert.That(archive.AtEnd, Is.True);
            Assert.That(volume.HasDecoded(nameof(volume.FragmentDelta)), Is.True);
            Assert.That(volume.FragmentDelta!.BaseKey, Is.EqualTo(baseKey));
            Assert.That(volume.FragmentDelta.Updates, Has.Count.EqualTo(count));
            Assert.That(volume.FragmentDelta.Updates.All(f => f.ConvexHullPoints is { Length: >= 3 }), Is.True);
        });
    }

    [Test]
    public void TruncatedFragmentInfoFails()
    {
        using var archive = new BitArchiveReader(File.ReadAllBytes(Fixture(0)).AsMemory(), 4528);
        var context = new FieldDecodeContext();
        Assert.Catch(() => new GroundVolumeFragmentInfoDecoder().Decode(ref context, archive));
    }

    [Test]
    public void PatchVolumeResolvesToNativeDescriptorAndFragmentInfoCache()
    {
        var catalog = ValorantDescriptors.CreateCatalog();
        Assert.That(catalog.SubobjectClassPaths["PatchVolume"], Is.EqualTo(GroundVolumeComponentDescriptor.ExportPath));
        var cache = catalog.ClassNetCacheDescriptors.Single(d => d.Path == GroundVolumeComponentDescriptor.ExportPath + "_ClassNetCache");
        Assert.That(cache.FunctionFields.Single().Name, Is.EqualTo("FragmentInfo"));
    }

    private static string Fixture(int index) => Path.Combine(TestContext.CurrentContext.TestDirectory,
        "Fixtures", "Brimstone", $"fragment-{index}.bin");
}