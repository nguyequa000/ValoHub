using Replay.Encoding.Archives;
using Replay.Encoding.Net;
using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Unreal.Tests.Parsing;

public class PrimitiveDecodersScalarTests
{
    [Test]
    public void Double_ReadsEightByteFloat()
    {
        var archive = CreateArchive(writer => writer.WriteDouble(123.5));
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.Double.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Double));
            Assert.That(value.DoubleValue, Is.EqualTo(123.5));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void FString_ReadsUnrealString()
    {
        var archive = CreateArchive(writer => writer.WriteFString("Spike"));
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.FString.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.String));
            Assert.That(value.StringValue, Is.EqualTo("Spike"));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void FName_ReadsInlineName()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteBit(false);
            writer.WriteFString("Bomb");
            writer.WriteInt32(0);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.FName.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.String));
            Assert.That(value.StringValue, Is.EqualTo("Bomb"));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void ByteArray_ReadsPackedCountAndBytes()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteIntPacked(3);
            writer.WriteByte(0x10);
            writer.WriteByte(0x20);
            writer.WriteByte(0x30);
        });
        var context = new FieldDecodeContext { FieldName = "InputEventData" };

        var value = PrimitiveDecoders.ByteArray(8).Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Object));
            Assert.That(value.ObjectValue, Is.EqualTo(new byte[] { 0x10, 0x20, 0x30 }));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void Guid_ReadsFourLittleEndianUnrealWords()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteUInt32(0x00112233u);
            writer.WriteUInt32(0x44556677u);
            writer.WriteUInt32(0x8899AABBu);
            writer.WriteUInt32(0xCCDDEEFFu);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.Guid.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Guid));
            Assert.That(value.GuidValue, Is.EqualTo(Guid.Parse("00112233-4455-6677-8899-aabbccddeeff")));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void SerializedInt_ReadsValueUsingKnownMaximum()
    {
        var archive = new BitArchiveReader(new byte[] { 0x05 }, bitCount: 4);
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.SerializedInt(maxValue: 16).Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.UInt32));
            Assert.That(value.UInt32Value, Is.EqualTo(5));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void UInt64_ReadsEightByteUnsignedInteger()
    {
        var archive = CreateArchive(writer => writer.WriteUInt64(0x0102030405060708UL));
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.UInt64.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.UInt64));
            Assert.That(value.UInt64Value, Is.EqualTo(0x0102030405060708UL));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void EnumRemainingBits_ReadsAllRemainingBits()
    {
        var archive = CreateArchive(writer => writer.WriteBits(3, 3));
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.EnumRemainingBits.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.UInt32));
            Assert.That(value.UInt32Value, Is.EqualTo(3));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void GameplayTag_ReadsPackedIndexAndResolvesTagName()
    {
        var archive = CreateArchive(writer => writer.WriteIntPacked(252));
        var context = new FieldDecodeContext
        {
            NetGuidCache = CreateTagCache(252, "FiringState.NumProjectiles"),
        };

        var value = PrimitiveDecoders.GameplayTag.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.GameplayTag));
            Assert.That(value.GameplayTagValue.TagIndex, Is.EqualTo(252));
            Assert.That(value.GameplayTagValue.TagName, Is.EqualTo("FiringState.NumProjectiles"));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void ObjectNetGuid_ResolvesFullPathFromCache()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "/Game/Weapons/Pistol");
        cache.SetNetGuidPath(5, "Pistol_C", new NetworkGuid(3));
        var archive = CreateArchive(writer => writer.WriteIntPacked(5));
        var context = new FieldDecodeContext
        {
            NetGuidCache = cache,
        };

        var value = PrimitiveDecoders.ObjectNetGuid.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.ObjectReference));
            Assert.That(value.ObjectReferenceValue, Is.EqualTo(new NetObjectReference(5, "/Game/Weapons/Pistol.Pistol_C")));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void ObjectNetGuid_LeavesPathNullForUnknownObject()
    {
        var archive = CreateArchive(writer => writer.WriteIntPacked(5));
        var context = new FieldDecodeContext
        {
            NetGuidCache = new NetGuidCache(),
        };

        var value = PrimitiveDecoders.ObjectNetGuid.Decode(ref context, archive);

        Assert.That(value.ObjectReferenceValue, Is.EqualTo(new NetObjectReference(5, null)));
    }

    private static NetGuidCache CreateTagCache(uint tagIndex, string tagName)
    {
        var exports = new NetFieldExport?[checked((int)tagIndex + 1)];
        exports[tagIndex] = new NetFieldExport
        {
            Handle = tagIndex,
            CompatibleChecksum = 0,
            Name = tagName,
        };

        var cache = new NetGuidCache();
        cache.AddExportGroup(new NetFieldExportGroup
        {
            PathName = "NetworkGameplayTagNodeIndex",
            PathNameIndex = 1,
            NetFieldExports = exports,
        });
        return cache;
    }

    private static BitArchiveReader CreateArchive(Action<BitWriter> write)
    {
        var writer = new BitWriter();
        write(writer);
        return new BitArchiveReader(writer.ToArray(), writer.BitCount);
    }

    private sealed class BitWriter
    {
        private readonly List<bool> _bits = [];

        public int BitCount => _bits.Count;

        public void WriteBit(bool value) => _bits.Add(value);

        public void WriteByte(byte value)
        {
            for (var i = 0; i < 8; i++)
            {
                _bits.Add((value & (1 << i)) != 0);
            }
        }

        public void WriteBits(int value, int bitCount)
        {
            for (var i = 0; i < bitCount; i++)
            {
                _bits.Add((value & (1 << i)) != 0);
            }
        }

        public void WriteInt32(int value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        public void WriteUInt32(uint value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        public void WriteUInt64(ulong value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        public void WriteDouble(double value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        public void WriteFString(string value)
        {
            var bytes = System.Text.Encoding.UTF8.GetBytes(value + "\0");
            WriteInt32(bytes.Length);
            foreach (var b in bytes)
            {
                WriteByte(b);
            }
        }

        public void WriteIntPacked(uint value)
        {
            do
            {
                var byteVal = (byte)((value & 0x7F) << 1);
                value >>= 7;
                if (value != 0)
                {
                    byteVal |= 1;
                }

                WriteByte(byteVal);
            } while (value != 0);
        }

        public byte[] ToArray()
        {
            var bytes = new byte[(_bits.Count + 7) / 8];
            for (var i = 0; i < _bits.Count; i++)
            {
                if (_bits[i])
                {
                    bytes[i >> 3] |= (byte)(1 << (i & 7));
                }
            }

            return bytes;
        }
    }
}
