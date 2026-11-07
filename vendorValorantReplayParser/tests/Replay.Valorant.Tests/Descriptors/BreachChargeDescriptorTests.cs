using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors;
using Replay.Valorant.Descriptors.Agents.Breach;

namespace Replay.Valorant.Tests.Descriptors;

public sealed class BreachChargeDescriptorTests
{
    [TestCase(ExportCategory.Ability)]
    [TestCase(ExportCategory.Effects)]
    public void FaultLineOwnershipRemainsEnabledForAbilityAndEffectsProfiles(ExportCategory category)
    {
        var catalog = new DescriptorCatalog();
        catalog.Add(new BreachFaultLineDescriptor());
        var registry = new ExportBindingRegistry(catalog, new ParseProfile { EnabledCategories = category });
        registry.OnExportGroupAdded(new NetFieldExportGroup
        {
            PathName = BreachFaultLineChargeParameters.ActorPath,
            PathNameIndex = 1,
            NetFieldExports =
            [
                new NetFieldExport { Handle = 0, Name = "Owner", CompatibleChecksum = 0 },
                new NetFieldExport { Handle = 1, Name = "Instigator", CompatibleChecksum = 0 },
            ],
        });

        var bound = registry.GetBoundGroup(BreachFaultLineChargeParameters.ActorPath)!;
        Assert.Multiple(() =>
        {
            Assert.That(bound.Enabled, Is.True);
            Assert.That(bound.FieldsByHandle[0].Enabled, Is.True);
            Assert.That(bound.FieldsByHandle[1].Enabled, Is.True);
        });
    }

    [Test]
    public void RecordedCacheRegistersChargeAndDetonationTogether()
    {
        var catalog = ValorantDescriptors.CreateCatalog();
        var cache = catalog.ClassNetCacheDescriptors.Single(d => d.Path == BreachFaultLineChargeParameters.ActorPath + "_ClassNetCache");
        Assert.That(cache.FunctionFields.Select(f => (f.Name, f.Handle)), Is.EquivalentTo(new[]
            { ("DetonateZone", (uint?)0), ("SetSizeFromCharge", (uint?)1) }));
        Assert.That(cache.FunctionFields.Single(f => f.Name == "SetSizeFromCharge").FunctionExportPath,
            Is.EqualTo(new BreachFaultLineChargeParameters().Path));
    }

    [TestCase(0d)]
    [TestCase(0.5d)]
    [TestCase(1d)]
    public void ChargePreservesZeroAndConsumesExactlyOneScalar(double charge)
    {
        var descriptor = new BreachFaultLineChargeParameters();
        Assert.That(descriptor.ChargePercent, Is.Null);
        Assert.That(descriptor.Grammar, Is.EqualTo(FieldStreamGrammar.FunctionParameters));
        using var archive = new BitArchiveReader(BitConverter.GetBytes(charge));
        var context = new FieldDecodeContext();
        var result = ((IFieldDecoder)descriptor.Fields.Single().Decoder!).Decode(ref context, archive);
        Assert.That(result.DoubleValue, Is.EqualTo(charge));
        Assert.That(archive.AtEnd, Is.True);
    }

    [Test]
    public void CharacterLocationIsABoundedNativeVectorAndAbsentRemainsUnknown()
    {
        var descriptor = new BreachFaultLineDescriptor();
        Assert.That(descriptor.CharacterLocation, Is.Null);
        var decoder = (IFieldDecoder)descriptor.Fields.Single(f => f.PropertyName == "CharacterLocation").Decoder!;
        byte[] bytes = [.. BitConverter.GetBytes(0d), .. BitConverter.GetBytes(-65.5287d), .. BitConverter.GetBytes(1300.2985d)];
        using var archive = new BitArchiveReader(bytes);
        var context = new FieldDecodeContext();
        var point = decoder.Decode(ref context, archive).VectorValue;
        Assert.That((point.X, point.Y, point.Z), Is.EqualTo((0d, -65.5287d, 1300.2985d)));
        Assert.That(archive.AtEnd, Is.True);
        using var truncated = new BitArchiveReader(bytes, 191);
        Assert.Throws<ArchiveReadException>(() =>
        {
            var innerContext = new FieldDecodeContext(); decoder.Decode(ref innerContext, truncated);
        });
    }

    [Test]
    public void TruncatedChargeCannotReadBeyondItsPayload()
    {
        var descriptor = new BreachFaultLineChargeParameters();
        using var archive = new BitArchiveReader(BitConverter.GetBytes(0.5d), 63);
        Assert.Throws<ArchiveReadException>(() =>
        {
            var context = new FieldDecodeContext();
            ((IFieldDecoder)descriptor.Fields.Single().Decoder!).Decode(ref context, archive);
        });
    }
}
