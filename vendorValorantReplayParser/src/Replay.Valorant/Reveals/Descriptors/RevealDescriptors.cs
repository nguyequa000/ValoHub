using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Unreal.Parsing;
using Replay.Valorant.Descriptors.Agents;

namespace Replay.Valorant.Reveals.Descriptors;

public sealed class SovaRevealProjectileDescriptor : SparseAbilityProjectileDescriptor<SovaRevealProjectileDescriptor>
{
    public const string DescriptorPath = "/Game/Characters/Hunter/S0/Ability_Q/Projectile_Hunter_Q_RevealBolt.Projectile_Hunter_Q_RevealBolt_C";
    public override string Path => DescriptorPath;
}

public sealed class FadeRevealProjectileDescriptor : SparseAbilityProjectileDescriptor<FadeRevealProjectileDescriptor>
{
    public const string DescriptorPath = "/Game/Characters/BountyHunter/S0/Ability_E/Projectile_E_BountyHunter_Divebomb.Projectile_E_BountyHunter_Divebomb_C";
    public override string Path => DescriptorPath;
}

public abstract class RevealDeviceDescriptor<T> : ExportGroupDescriptor<T>
    where T : RevealDeviceDescriptor<T>
{
    public override ExportCategory Categories => ExportCategory.Ability | ExportCategory.Effects;
    public override ExportGroupKind Kind => ExportGroupKind.Actor;
    public NetObjectReference? Owner { get; set; }
    public NetObjectReference? Instigator { get; set; }
    protected override void Configure()
    {
        AddPropertyHandle(11, x => x.Owner, ExportCategory.Ability).ObjectNetGuid();
        AddPropertyHandle(13, x => x.Instigator, ExportCategory.Ability).ObjectNetGuid();
    }
}

public sealed class SovaRevealDeviceDescriptor : RevealDeviceDescriptor<SovaRevealDeviceDescriptor>
{
    public const string DescriptorPath = "/Game/Characters/Hunter/S0/Ability_Q/GameObject_Hunter_Q_SonarBolt.GameObject_Hunter_Q_SonarBolt_C";
    public override string Path => DescriptorPath;
}

public sealed class SovaRevealPulseDescriptor : RevealDeviceDescriptor<SovaRevealPulseDescriptor>
{
    public const string DescriptorPath = "/Game/Characters/Hunter/S0/Ability_Q/GameObject_Hunter_Q_SonarPing.GameObject_Hunter_Q_SonarPing_C";
    public override string Path => DescriptorPath;
}

public sealed class FadeRevealDeviceDescriptor : RevealDeviceDescriptor<FadeRevealDeviceDescriptor>
{
    public const string DescriptorPath = "/Game/Characters/BountyHunter/S0/Ability_E/GameObject_BountyHunter_E_LoSReveal_Source_Reactivate.GameObject_BountyHunter_E_LoSReveal_Source_Reactivate_C";
    public override string Path => DescriptorPath;
}
