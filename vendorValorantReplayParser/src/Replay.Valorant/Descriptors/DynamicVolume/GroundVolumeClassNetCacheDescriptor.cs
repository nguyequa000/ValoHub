using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Events;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors.DynamicVolume;

/// <summary>Native FastArray fragment updates transmitted independently of replicated properties.</summary>
public sealed class GroundVolumeClassNetCacheDescriptor : ClassNetCacheDescriptor
{
    public GroundVolumeClassNetCacheDescriptor() : base(GroundVolumeComponentDescriptor.ExportPath + "_ClassNetCache",
        [new RpcDescriptor
        {
            Name = "FragmentInfo", Handle = 0, Categories = ExportCategory.Ability,
            FunctionExportPath = GroundVolumeComponentDescriptor.ExportPath + ":FragmentInfo",
            Decoder = new GroundVolumeFragmentInfoDecoder(),
        }]) { }
}

public sealed class GroundVolumeFragmentInfoDecoder : IRpcDecoder
{
    public DecodedPayloadResult Decode(ref FieldDecodeContext context, FBitArchive archive)
    {
        var payload = new GroundVolumeComponentDescriptor();
        payload.ReadFragmentDelta(ref context, archive);
        return new(payload, 1, []);
    }
}