using Replay.Encoding.Archives;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors.Agents;
using Replay.Valorant.Descriptors.Agents.Terra;

namespace Replay.Valorant.Tests.Descriptors;

public sealed class TerraDescriptorTests
{
    private const string RefractPath =
        "/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C";

    [Test]
    public void RecordedRefractRouteDecodesObservedWorldPointsAtExactBoundary()
    {
        var descriptor = TerraDescriptors.CreateDescriptors().OfType<AbilityActorDescriptor>()
            .Single(d => d.Path == RefractPath);
        var field = descriptor.Fields.Single(f => f.PropertyName == nameof(descriptor.RewindPath));
        const string payload = "BAKCgQIAAAAAMwSvwAAAAMB+WZXAAAAAIO42gkAABIKBAgAAAIDLlq/AAAAA4OYsoMAAwAf8n7+CQAAA";
        using var archive = new BitArchiveReader(Convert.FromBase64String(payload), 480);
        var context = new FieldDecodeContext();

        var value = ((IFieldDecoder)field.Decoder!).Decode(ref context, archive);
        var points = (WaylayRewindPathPointDescriptor[])value.ObjectValue!;

        Assert.Multiple(() =>
        {
            Assert.That(archive.AtEnd, Is.True);
            Assert.That(points, Has.Length.EqualTo(2));
            Assert.That(points[0].Position!.Value.X, Is.EqualTo(-3970.099609375));
            Assert.That(points[0].Position!.Value.Y, Is.EqualTo(-1366.373779296875));
            Assert.That(points[1].Position!.Value.X, Is.EqualTo(-4043.3974609375));
            Assert.That(points[1].Position!.Value.Y, Is.EqualTo(-2070.450927734375));
        });
    }

    [Test]
    public void TruncatedRefractRouteIsRejected()
    {
        var descriptor = TerraDescriptors.CreateDescriptors().OfType<AbilityActorDescriptor>()
            .Single(d => d.Path == RefractPath);
        var field = descriptor.Fields.Single(f => f.PropertyName == nameof(descriptor.RewindPath));
        const string payload = "BAKCgQIAAAAAMwSvwAAAAMB+WZXAAAAAIO42gkAABIKBAgAAAIDLlq/AAAAA4OYsoMAAwAf8n7+CQAAA";
        using var archive = new BitArchiveReader(Convert.FromBase64String(payload), 479);
        var context = new FieldDecodeContext();

        Assert.Throws<ArchiveReadException>(() => ((IFieldDecoder)field.Decoder!).Decode(ref context, archive));
    }
}
