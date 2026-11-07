using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Unreal.Parsing;
using Replay.Valorant.Combat;

namespace Replay.Valorant.Tests.Combat;

public class DamageEquippableTests
{
    [Test]
    public void EquippableUsed_DecodesNativeGuid()
    {
        var field = new MulticastNotifyDamagePointParameters().Fields
            .Single(candidate => candidate.PropertyName == "EquippableUsed");
        var decoder = (IFieldDecoder)field.Decoder!;
        var context = new FieldDecodeContext();
        using var archive = new BitArchiveReader(new byte[] { 0x22 }, bitCount: 8);

        var value = decoder.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.ObjectReference));
            Assert.That(value.ObjectReferenceValue.NetGuid, Is.EqualTo(17));
            Assert.That(archive.AtEnd, Is.True);
        });
    }
}
