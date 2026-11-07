using Replay.Encoding.Archives;
using static Replay.Encoding.PayloadEncryption.ValorantSeededTransformHelpers;

namespace Replay.Encoding.PayloadEncryption.VersionedTransforms;

// Recovered from the global 13.08 executable, FUN_144539860.
public sealed class ValorantSeededTransform13_08 : IPayloadTransform
{
    private const uint SeedAddend = 0x3c17a9fau;
    private const byte TailXor = 0xfa;

    public IReadOnlyCollection<string> SupportedReplayVersions { get; } = ["++Ares-Core+release-13.08"];

    public int GetOutputByteCount(int bitCount) => ValorantSeededTransformHelpers.GetOutputByteCount(bitCount);

    public void Apply(FBitArchive input, uint seed, Span<byte> output)
    {
        var bitCount = CopyInputToOutput(input, output);
        Transform(output[..GetOutputByteCount(bitCount)], bitCount, seed);
    }

    public void Apply(FBitArchive input, int bitCount, uint seed, Span<byte> output)
    {
        CopyInputToOutput(input, bitCount, output);
        Transform(output[..GetOutputByteCount(bitCount)], bitCount, seed);
    }

    private static void Transform(Span<byte> output, int bitCount, uint seed)
    {
        if (bitCount == 0)
        {
            return;
        }

        var state = seed;
        var streamByte = (byte)seed;
        var prngA = InitialPrngA(seed, SeedAddend, -6);
        var prngB = InitialPrngB(seed);
        var byteOffset = 0;
        var bitsRemaining = bitCount;

        while (bitsRemaining > 63)
        {
            var value = TransformUInt64(ReadUInt64(output, byteOffset), state);
            WriteUInt64(output, byteOffset, value);
            AdvanceTransformState(ref state, ref prngA, ref prngB, out streamByte);
            byteOffset += 8;
            bitsRemaining -= 64;
        }

        while (bitsRemaining > 31)
        {
            var value = TransformUInt32(ReadUInt32(output, byteOffset), state);
            WriteUInt32(output, byteOffset, value);
            AdvanceTransformState(ref state, ref prngA, ref prngB, out streamByte);
            byteOffset += 4;
            bitsRemaining -= 32;
        }

        while (bitsRemaining > 7)
        {
            output[byteOffset] = TransformByte(output[byteOffset], state);
            AdvanceTransformState(ref state, ref prngA, ref prngB, out streamByte);
            byteOffset++;
            bitsRemaining -= 8;
        }

        if (bitsRemaining != 0)
        {
            var mask = (byte)(0xff >> (7 - ((bitCount - 1) & 7)));
            output[byteOffset] ^= (byte)(mask & (streamByte ^ TailXor));
        }
    }

    private static ulong TransformUInt64(ulong value, uint state)
    {
        unchecked
        {
            value = ReverseBits64WithoutFinal16BitSwap(value + RotateRight(state, 8));
            value = RotateLeft(value, (int)(RotateRight(state, 6) % 63) + 1);
            value = SubstituteBytes(value, SubstituteTable64);
            value = RotateRight(value, (int)(RotateRight(state, 4) % 63) + 1);
            value = SubstituteBytes(value, SubstituteTable64);
            value = SubstituteBytes(value, SubstituteTable64);
            return ReverseBits64WithoutFinal16BitSwap(value);
        }
    }

    private static uint TransformUInt32(uint value, uint state)
    {
        unchecked
        {
            value = ReverseBits32(value + RotateLeft(state, 8));
            value = RotateLeft(value, (int)(RotateLeft(state, 6) % 31) + 1);
            value = SubstituteBytes(value, SubstituteTable32);
            value = RotateRight(value, (int)(RotateLeft(state, 4) % 31) + 1);
            value = SubstituteBytes(value, SubstituteTable32);
            value = SubstituteBytes(value, SubstituteTable32);
            return ReverseBits32(value);
        }
    }

    private static byte TransformByte(byte value, uint state)
    {
        unchecked
        {
            var mix6 = state * 0x1b0829u;
            var mix4 = state * 0x3931u;

            value = ReverseBits8((byte)(value + (byte)(mix6 * 0x79u)));
            value = RotateLeft(value, (int)(mix6 % 7) + 1);
            value = SubstituteTable8[value];
            value = RotateRight(value, (int)(mix4 % 7) + 1);
            value = SubstituteTable8[value];
            value = SubstituteTable8[value];
            return ReverseBits8(value);
        }
    }
}
