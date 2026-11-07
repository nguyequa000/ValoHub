using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.GameState;

public abstract class ArmorItemDescriptor<TDescriptor> : ExportGroupDescriptor<TDescriptor>
    where TDescriptor : ArmorItemDescriptor<TDescriptor>
{
    public override ExportCategory Categories => ExportCategory.Inventory | ExportCategory.Gunplay;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;

    public int MaximumAmount { get; set; }
    public NetObjectReference Owner { get; set; }
    public NetObjectReference MyPawn { get; set; }
    public byte InInventory { get; set; }
    public NetObjectReference AttachedDamageSection { get; set; }

    protected override void Configure()
    {
        AddProperty(x => x.MaximumAmount).Int32();
        AddProperty(x => x.Owner).ObjectNetGuid();
        AddProperty(x => x.MyPawn).ObjectNetGuid();
        AddProperty(x => x.InInventory).EnumByte();
        AddProperty(x => x.AttachedDamageSection).ObjectNetGuid();
    }
}