using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Unreal.Tests.Parsing;

public class PrimitiveDecodersVectorTests
{
    [Test]
    public void NormalizedQuaternionPreservesDoublesAndReconstructsPositiveW()
    {
        using var archive = CreateArchive(writer =>
        {
            writer.WriteDouble(0.123456789012345);
            writer.WriteDouble(0);
            writer.WriteDouble(-0.5);
        });
        var context = new FieldDecodeContext();
        var value = (FQuat)PrimitiveDecoders.NormalizedQuaternion.Decode(ref context, archive).ObjectValue!;
        Assert.Multiple(() =>
        {
            Assert.That(value.X, Is.EqualTo(0.123456789012345));
            Assert.That(value.Z, Is.EqualTo(-0.5));
            Assert.That(value.W, Is.EqualTo(Math.Sqrt(0.75 - value.X * value.X)).Within(1e-15));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase(double.NaN)]
    [TestCase(double.PositiveInfinity)]
    [TestCase(2.0)]
    public void NormalizedQuaternionRejectsInvalidComponents(double x)
    {
        using var archive = CreateArchive(writer =>
        {
            writer.WriteDouble(x);
            writer.WriteDouble(0);
            writer.WriteDouble(0);
        });
        var context = new FieldDecodeContext();
        var exception = Assert.Throws<ArchiveReadException>(() => PrimitiveDecoders.NormalizedQuaternion.Decode(ref context, archive));
        Assert.That(exception!.ErrorCode, Is.EqualTo(ArchiveErrorCode.InvalidData));
    }

    [Test]
    public void NormalizedQuaternionRejectsTruncatedComponents()
    {
        using var archive = new BitArchiveReader(new byte[24], 191);
        var context = new FieldDecodeContext();
        Assert.Throws<ArchiveReadException>(() => PrimitiveDecoders.NormalizedQuaternion.Decode(ref context, archive));
    }

    [Test]
    public void Vector_ReadsUnquantizedDoubleVector()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteDouble(1.25);
            writer.WriteDouble(-2.5);
            writer.WriteDouble(3.75);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.Vector.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Vector));
            AssertVector(value.VectorValue, 1.25, -2.5, 3.75);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void VectorFloat_ReadsUnquantizedFloatVector()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteSingle(1.25f);
            writer.WriteSingle(-2.5f);
            writer.WriteSingle(3.75f);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.VectorFloat.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Vector));
            AssertVector(value.VectorValue, 1.25, -2.5, 3.75);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [TestCase(nameof(PrimitiveDecoders.VectorNetQuantize), 1, 10.0, -2.0, 3.0, 6)]
    [TestCase(nameof(PrimitiveDecoders.VectorNetQuantize10), 10, 1.2, -3.4, 5.6, 7)]
    [TestCase(nameof(PrimitiveDecoders.VectorNetQuantize100), 100, 1.23, -4.56, 7.89, 11)]
    public void QuantizedVector_ReadsPackedVector(
        string decoderName,
        int scaleFactor,
        double x,
        double y,
        double z,
        int componentBitCount)
    {
        var archive = CreateArchive(writer =>
            writer.WriteQuantizedVector(x, y, z, scaleFactor, componentBitCount));
        var context = new FieldDecodeContext();

        var value = GetVectorDecoder(decoderName).Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Vector));
            AssertVector(value.VectorValue, x, y, z);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void VectorNetQuantizeNormal_ReadsFixedNormalVector()
    {
        var archive = CreateArchive(writer => writer.WriteFixedNormalVector(0.5, -1.0, 1.0));
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.VectorNetQuantizeNormal.Decode(ref context, archive);

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.Vector));
            AssertVector(value.VectorValue, 16384.0 / 32767.0, -1.0, 1.0);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void RepMovement_DecodesRequiredFields()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteQuantizedVector(1.23, -4.56, 7.89, scaleFactor: 100, componentBitCount: 11);
            writer.WriteCompressedShortRotatorComponent(0);
            writer.WriteCompressedShortRotatorComponent(0);
            writer.WriteCompressedShortRotatorComponent(0);
            writer.WriteQuantizedVector(10, -2, 3, scaleFactor: 1, componentBitCount: 6);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.RepMovement.Decode(ref context, archive);
        var movement = value.RepMovementValue;

        Assert.Multiple(() =>
        {
            Assert.That(value.Kind, Is.EqualTo(DecodedFieldValueKind.RepMovement));
            AssertVector(movement.Location!.Value, 1.23, -4.56, 7.89);
            AssertVector(movement.LinearVelocity!.Value, 10, -2, 3);
            Assert.That(movement.Rotation, Is.EqualTo(new FRotator(0, 0, 0)));
            Assert.That(movement.AngularVelocity, Is.Null);
            Assert.That(movement.bSimulatedPhysicsSleep, Is.False);
            Assert.That(movement.bRepPhysics, Is.False);
            Assert.That(movement.ServerFrame, Is.Zero);
            Assert.That(movement.ServerPhysicsHandle, Is.Zero);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void RepMovement_DecodesOptionalFields()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteBit(true);
            writer.WriteBit(true);
            writer.WriteBit(true);
            writer.WriteBit(true);
            writer.WriteQuantizedVector(1.23, -4.56, 7.89, scaleFactor: 100, componentBitCount: 11);
            writer.WriteCompressedShortRotatorComponent(16384);
            writer.WriteCompressedShortRotatorComponent(32768);
            writer.WriteCompressedShortRotatorComponent(49152);
            writer.WriteQuantizedVector(10, -2, 3, scaleFactor: 1, componentBitCount: 6);
            writer.WriteQuantizedVector(-4, 5, -6, scaleFactor: 1, componentBitCount: 5);
            writer.WriteIntPacked(123);
            writer.WriteIntPacked(456);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders.RepMovement.Decode(ref context, archive);
        var movement = value.RepMovementValue;

        Assert.Multiple(() =>
        {
            AssertVector(movement.AngularVelocity!.Value, -4, 5, -6);
            Assert.That(movement.Rotation!.Value.Pitch, Is.EqualTo(90).Within(1e-6));
            Assert.That(movement.Rotation.Value.Yaw, Is.EqualTo(180).Within(1e-6));
            Assert.That(movement.Rotation.Value.Roll, Is.EqualTo(270).Within(1e-6));
            Assert.That(movement.bSimulatedPhysicsSleep, Is.True);
            Assert.That(movement.bRepPhysics, Is.True);
            Assert.That(movement.ServerFrame, Is.EqualTo(123));
            Assert.That(movement.ServerPhysicsHandle, Is.EqualTo(456));
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void RepMovement_DecodesByteQuantizedRotation()
    {
        var archive = CreateArchive(writer =>
        {
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteBit(false);
            writer.WriteQuantizedVector(1.23, -4.56, 7.89, scaleFactor: 100, componentBitCount: 11);
            writer.WriteCompressedByteRotatorComponent(64);
            writer.WriteCompressedByteRotatorComponent(128);
            writer.WriteCompressedByteRotatorComponent(192);
            writer.WriteQuantizedVector(10, -2, 3, scaleFactor: 1, componentBitCount: 6);
        });
        var context = new FieldDecodeContext();

        var value = PrimitiveDecoders
            .RepMovementWithRotation(ERotatorQuantization.ByteComponents)
            .Decode(ref context, archive);
        var movement = value.RepMovementValue;

        Assert.Multiple(() =>
        {
            Assert.That(movement.Rotation!.Value.Pitch, Is.EqualTo(90).Within(1e-6));
            Assert.That(movement.Rotation.Value.Yaw, Is.EqualTo(180).Within(1e-6));
            Assert.That(movement.Rotation.Value.Roll, Is.EqualTo(270).Within(1e-6));
            AssertVector(movement.LinearVelocity!.Value, 10, -2, 3);
            Assert.That(archive.AtEnd, Is.True);
        });
    }

    [Test]
    public void RepMovementWithRotation_UnsupportedQuantization_Throws()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            PrimitiveDecoders.RepMovementWithRotation((ERotatorQuantization)int.MaxValue));
    }

    [TestCase(1, 10.0, -2.0, 3.0, 6)]
    [TestCase(10, 1.2, -3.4, 5.6, 7)]
    [TestCase(100, 1.23, -4.56, 7.89, 11)]
    public void ReadQuantizedVector_DecodesPackedScaledComponents(
        int scaleFactor,
        double x,
        double y,
        double z,
        int componentBitCount)
    {
        var archive = CreateArchive(writer =>
            writer.WriteQuantizedVector(x, y, z, scaleFactor, componentBitCount));

        var vector = ArchiveVectorReaders.ReadQuantizedVector(archive, scaleFactor);

        AssertVector(vector, x, y, z);
        Assert.That(vector.Bits, Is.EqualTo(componentBitCount));
        Assert.That(vector.ScaleFactor, Is.EqualTo(scaleFactor));
        Assert.That(archive.AtEnd, Is.True);
    }

    [Test]
    public void ReadFixedVectorNormal_DecodesFixedComponents()
    {
        var archive = CreateArchive(writer => writer.WriteFixedNormalVector(0.5, -1.0, 1.0));

        var vector = ArchiveVectorReaders.ReadFixedVectorNormal(archive);

        AssertVector(vector, 16384.0 / 32767.0, -1.0, 1.0);
        Assert.That(vector.Bits, Is.EqualTo(16));
        Assert.That(vector.ScaleFactor, Is.EqualTo(32767));
        Assert.That(archive.AtEnd, Is.True);
    }

    private static IFieldDecoder GetVectorDecoder(string decoderName) => decoderName switch
    {
        nameof(PrimitiveDecoders.VectorNetQuantize) => PrimitiveDecoders.VectorNetQuantize,
        nameof(PrimitiveDecoders.VectorNetQuantize10) => PrimitiveDecoders.VectorNetQuantize10,
        nameof(PrimitiveDecoders.VectorNetQuantize100) => PrimitiveDecoders.VectorNetQuantize100,
        _ => throw new ArgumentOutOfRangeException(nameof(decoderName), decoderName, null),
    };

    private static BitArchiveReader CreateArchive(Action<BitWriter> write)
    {
        var writer = new BitWriter();
        write(writer);
        return new BitArchiveReader(writer.ToArray(), writer.BitCount);
    }

    private static void AssertVector(FVector vector, double x, double y, double z)
    {
        Assert.Multiple(() =>
        {
            Assert.That(vector.X, Is.EqualTo(x).Within(1e-9));
            Assert.That(vector.Y, Is.EqualTo(y).Within(1e-9));
            Assert.That(vector.Z, Is.EqualTo(z).Within(1e-9));
        });
    }

    private sealed class BitWriter
    {
        private readonly List<bool> _bits = [];

        public int BitCount => _bits.Count;

        public void WriteBit(bool value) => _bits.Add(value);

        public void WriteSerializedInt(uint value, int maxValue)
        {
            uint writtenValue = 0;
            for (uint mask = 1; writtenValue + mask < maxValue; mask <<= 1)
            {
                var bit = (value & mask) != 0;
                _bits.Add(bit);
                if (bit)
                {
                    writtenValue |= mask;
                }
            }
        }

        public void WriteQuantizedVector(
            double x,
            double y,
            double z,
            int scaleFactor,
            int componentBitCount)
        {
            var info = (uint)(componentBitCount | (1 << 6));
            WriteSerializedInt(info, 1 << 7);
            WriteSignedBits(RoundToInt(x * scaleFactor), componentBitCount);
            WriteSignedBits(RoundToInt(y * scaleFactor), componentBitCount);
            WriteSignedBits(RoundToInt(z * scaleFactor), componentBitCount);
        }

        public void WriteFixedNormalVector(double x, double y, double z)
        {
            WriteFixedNormalComponent(x);
            WriteFixedNormalComponent(y);
            WriteFixedNormalComponent(z);
        }

        public void WriteCompressedShortRotatorComponent(ushort value)
        {
            WriteBit(value != 0);
            if (value != 0)
            {
                WriteUInt16(value);
            }
        }

        public void WriteCompressedByteRotatorComponent(byte value)
        {
            WriteBit(value != 0);
            if (value != 0)
            {
                WriteByte(value);
            }
        }

        public void WriteIntPacked(uint value)
        {
            do
            {
                var byteValue = (byte)((value & 0x7F) << 1);
                value >>= 7;
                if (value != 0)
                {
                    byteValue |= 1;
                }

                WriteByte(byteValue);
            } while (value != 0);
        }

        public void WriteSingle(float value) => WriteUInt32(BitConverter.SingleToUInt32Bits(value));

        public void WriteDouble(double value) => WriteUInt64(BitConverter.DoubleToUInt64Bits(value));

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

        private void WriteFixedNormalComponent(double value)
        {
            const int bias = 1 << 15;
            const int maxDelta = (1 << 16) - 1;
            const int scale = bias - 1;

            var delta = RoundToInt(value * scale) + bias;
            delta = Math.Clamp(delta, 0, maxDelta);
            WriteSerializedInt((uint)delta, 1 << 16);
        }

        private void WriteUInt32(uint value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        private void WriteUInt16(ushort value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        private void WriteUInt64(ulong value)
        {
            foreach (var b in BitConverter.GetBytes(value))
            {
                WriteByte(b);
            }
        }

        private void WriteByte(byte value)
        {
            for (var i = 0; i < 8; i++)
            {
                _bits.Add((value & (1 << i)) != 0);
            }
        }

        private void WriteSignedBits(long value, int bitCount)
        {
            var mask = bitCount == 64 ? ulong.MaxValue : (1UL << bitCount) - 1;
            WriteBits(bitCount, (ulong)value & mask);
        }

        private void WriteBits(int count, ulong value)
        {
            for (var i = 0; i < count; i++)
            {
                _bits.Add((value & (1UL << i)) != 0);
            }
        }

        private static int RoundToInt(double value) =>
            (int)Math.Round(value, MidpointRounding.AwayFromZero);
    }
}
