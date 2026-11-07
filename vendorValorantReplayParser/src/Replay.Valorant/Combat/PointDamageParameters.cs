using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Unreal.Parsing;

namespace Replay.Valorant.Combat;

public abstract class PointDamageParameters<T> : DamageParameters<T> where T : PointDamageParameters<T>
{
    public NetObjectReference DamagedComponent { get; set; }
    public string? DamagedBone { get; set; }
    public bool IsWallPenetration { get; set; }
    public float FalloffMultiplier { get; set; }
    public FVector? DamageDirection { get; set; }
    public FVector? DamageImpactLocation { get; set; }
    public FVector? DamageImpactNormal { get; set; }
    public FVector? DamageImpactBoneRelativeLocation { get; set; }

    protected void AddPointFields(uint offset = 0)
    {
        AddPropertyHandle(26 + offset, x => x.DamagedComponent, ExportCategory.Gunplay).ObjectNetGuid();
        AddPropertyHandle(27 + offset, x => x.DamagedBone, ExportCategory.Gunplay).FName();
        AddPropertyHandle(28 + offset, "bIsWallPenetration", x => x.IsWallPenetration, ExportCategory.Gunplay).Bool();
        AddPropertyHandle(29 + offset, x => x.FalloffMultiplier, ExportCategory.Gunplay).Float();
        AddPropertyHandle(30 + offset, x => x.DamageDirection, ExportCategory.Gunplay).FVectorNetQuantizeNormal();
        AddPropertyHandle(31 + offset, x => x.DamageImpactLocation, ExportCategory.Gunplay).FVectorNetQuantize();
        AddPropertyHandle(32 + offset, x => x.DamageImpactNormal, ExportCategory.Gunplay).FVectorNetQuantizeNormal();
        AddPropertyHandle(33 + offset, x => x.DamageImpactBoneRelativeLocation, ExportCategory.Gunplay).FVectorNetQuantize();
    }
}
