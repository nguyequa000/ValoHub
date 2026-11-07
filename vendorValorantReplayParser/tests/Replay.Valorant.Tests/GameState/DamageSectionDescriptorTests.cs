using Replay.Encoding.Archives;
using Replay.Unreal.Parsing;
using Replay.Valorant.GameState;

namespace Replay.Valorant.Tests.GameState;

public sealed class DamageSectionDescriptorTests
{
    [TestCase("Life", 34.85313f)]
    [TestCase("MaximumLife", 50f)]
    public void LifeFloatsRetainRecordedPrecisionAndConsumeTheirWholePayload(string property, float amount)
    {
        var field = new ChildDamageSectionComponentDescriptor().Fields.Single(f => f.PropertyName == property);
        var decoder = (IFieldDecoder)field.Decoder!;
        using var archive = new BitArchiveReader(BitConverter.GetBytes(amount), 32);
        var context = new FieldDecodeContext();
        Assert.That(decoder.Decode(ref context, archive).FloatValue, Is.EqualTo(amount));
        Assert.That(archive.AtEnd, Is.True);
        using var truncated = new BitArchiveReader(new byte[3], 24);
        Assert.Throws<ArchiveReadException>(() => decoder.Decode(ref context, truncated));
    }
}