using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Descriptors;

/// <summary>A sparse object-reference array element, framed as a nested RepLayout field.</summary>
public sealed class ReplicatedObjectReference(uint handle, string exportName)
    : ExportGroupDescriptor<ReplicatedObjectReference>
{
    public NetObjectReference? NetGuid { get; set; }
    public override object CreatePayloadInstance() => new ReplicatedObjectReference(handle, exportName);
    protected override void Configure() => AddPropertyHandle(handle, exportName, x => x.NetGuid).ObjectNetGuid();
}
