using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Combat;

public sealed class DamageAssist : ExportGroupDescriptor<DamageAssist>
{
    private readonly uint _firstHandle = 35;
    public DamageAssist() { }
    public DamageAssist(uint firstHandle) => _firstHandle = firstHandle;
    public override object CreatePayloadInstance() => new DamageAssist(_firstHandle);
    public byte? AssistType { get; set; }
    public NetObjectReference? Assister { get; set; }
    public NetObjectReference? AssistingEquippableClass { get; set; }
    public FGameplayTag? AssistTag { get; set; }

    protected override void Configure()
    {
        AddPropertyHandle(_firstHandle, x => x.AssistType).EnumRemainingBits();
        AddPropertyHandle(_firstHandle + 1, x => x.Assister).ObjectNetGuid();
        AddPropertyHandle(_firstHandle + 2, x => x.AssistingEquippableClass).ObjectNetGuid();
        AddPropertyHandle(_firstHandle + 3, x => x.AssistTag).FGameplayTag();
    }
}
