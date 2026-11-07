using Replay.Models.Descriptors;
using Replay.Valorant.Descriptors;
using Replay.Valorant.Descriptors.Agents.Sequoia;

namespace Replay.Valorant.Tests.Descriptors;

public sealed class AbilityDescriptorCoverageTests
{
    private static readonly string[] MissingAbilityPaths =
    [
        "/Game/Characters/BountyHunter/S0/Ability_4/Ability_BountyHunter_4_WolfHoundBendable.Ability_BountyHunter_4_WolfHoundBendable_C",
        "/Game/Characters/BountyHunter/S0/Ability_E/Ability_E_BountyHunter_ReconDivebomb.Ability_E_BountyHunter_ReconDivebomb_C",
        "/Game/Characters/BountyHunter/S0/Ability_Q/Ability_Q_BountyHunter_TetherDivebomb.Ability_Q_BountyHunter_TetherDivebomb_C",
        "/Game/Characters/BountyHunter/S0/Ability_X/Ability_BountyHunter_X_WaveForm.Ability_BountyHunter_X_WaveForm_C",
        "/Game/Characters/Smonk/S0/Ability_4/ReactiveArmor/Ability_Smonk_BFArmor_Reactive.Ability_Smonk_BFArmor_Reactive_C",
        "/Game/Characters/Smonk/S0/Ability_Q/DebuffKnife/DecayLauncher/Ability_Q_Smonk_DebuffKnife.Ability_Q_Smonk_DebuffKnife_C",
        "/Game/Characters/Smonk/S0/Ability_X/ReactiveRes/Ability_Smonk_X_ReactiveRes_Engineering.Ability_Smonk_X_ReactiveRes_Engineering_C",
        "/Game/Characters/Sprinter/S0/Ability_4/Ability_Sprinter_4_Tunnel_Production.Ability_Sprinter_4_Tunnel_Production_C",
        "/Game/Characters/Sprinter/S0/Ability_E/Ability_Sprinter_E_Sprint_Production.Ability_Sprinter_E_Sprint_Production_C",
        "/Game/Characters/Sprinter/S0/Ability_Q/Ability_Sprinter_Q_GroundStrike_Production.Ability_Sprinter_Q_GroundStrike_Production_C",
        "/Game/Characters/Sprinter/S0/Ability_X/Ability_Sprinter_X_LightningGun_Production.Ability_Sprinter_X_LightningGun_Production_C",
        "/Game/Characters/Hunter/S0/Ability_4/Ability_Hunter_4_BoltExplosive.Ability_Hunter_4_BoltExplosive_C",
        "/Game/Characters/Hunter/S0/Ability_E/Drone/Ability_Hunter_E_DeployDrone.Ability_Hunter_E_DeployDrone_C",
        "/Game/Characters/Hunter/S0/Ability_Q/Ability_Hunter_Q_RevealBolt_Signature.Ability_Hunter_Q_RevealBolt_Signature_C",
        "/Game/Characters/Hunter/S0/Ability_X/Ability_Hunter_X_LaserMulti.Ability_Hunter_X_LaserMulti_C",
        "/Game/Characters/Stealth/S0/Ability_4/Ability_Stealth_4_Decoy.Ability_Stealth_4_Decoy_C",
        "/Game/Characters/Stealth/S0/Ability_E/Ability_Stealth_E_Teleport.Ability_Stealth_E_Teleport_C",
        "/Game/Characters/Stealth/S0/Ability_Q/Ability_Stealth_Q_BounceFlash.Ability_Stealth_Q_BounceFlash_C",
        "/Game/Characters/Stealth/S0/Ability_X/Ability_Stealth_X_Cloak_Equip.Ability_Stealth_X_Cloak_Equip_C",
        "/Game/Characters/Guide/S0/Ability_4/Ability_Guide_4_Heal.Ability_Guide_4_Heal_C",
        "/Game/Characters/Guide/S0/Ability_E/Ability_Guide_E_HawkFlash.Ability_Guide_E_HawkFlash_C",
        "/Game/Characters/Guide/S0/Ability_Q/Ability_Guide_Q_PossessableScout.Ability_Guide_Q_PossessableScout_C",
        "/Game/Characters/Guide/S0/Ability_X/Ability_Guide_X_Pack.Ability_Guide_X_Pack_C",
        "/Game/Characters/Wushu/S0/Ability_E/Ability_Wushu_E_Dash.Ability_Wushu_E_Dash_C",
        "/Game/Characters/Wushu/S0/Ability_Q/Ability_Wushu_Q_CycloneBoost.Ability_Wushu_Q_CycloneBoost_C",
        "/Game/Characters/Wushu/S0/Ability_X/Ability_Wushu_X_Dagger_Production.Ability_Wushu_X_Dagger_Production_C",
        "/Game/Characters/Iris/S0/Ability_4/Ability_Iris_Thumper.Ability_Iris_Thumper_C",
        "/Game/Characters/Iris/S0/Ability_Q/Ability_Iris_Harmonize.Ability_Iris_Harmonize_C",
        "/Game/Characters/Iris/S0/Ability_X/Ability_Iris_X_SonicWave.Ability_Iris_X_SonicWave_C",
        "/Game/Characters/Vampire/S0/Ability_4/Ability_Vampire_4_NearsightAoE.Ability_Vampire_4_NearsightAoE_C",
        "/Game/Characters/Vampire/S0/Ability_E/Ability_Vampire_E_Escape.Ability_Vampire_E_Escape_C",
        "/Game/Characters/Vampire/S0/Ability_Q/Ability_Vampire_Q_Heal.Ability_Vampire_Q_Heal_C",
        "/Game/Characters/Vampire/S0/Ability_X/Ability_Vampire_X_Frenzy.Ability_Vampire_X_Frenzy_C",
        "/Game/Characters/Breach/S0/Ability_4/Ability_Breach_4_FusionBlast.Ability_Breach_4_FusionBlast_C",
        "/Game/Characters/Breach/S0/Ability_E/Ability_Breach_E_Fissure.Ability_Breach_E_Fissure_C",
        "/Game/Characters/Breach/S0/Ability_Q/Ability_Breach_Q_Flash.Ability_Breach_Q_Flash_C",
        "/Game/Characters/Breach/S0/Ability_X/Ability_Breach_X_Shockwave.Ability_Breach_X_Shockwave_C",
        "/Game/Characters/Sequoia/S0/Ability_4/Ability_Sequoia_4_Wave.Ability_Sequoia_4_Wave_C",
        "/Game/Characters/Sequoia/S0/Ability_E/WreckingBall/Ability_Sequoia_E_WreckingBall.Ability_Sequoia_E_WreckingBall_C",
        "/Game/Characters/Sequoia/S0/Ability_Q/Ability_Sequoia_Q_FragileMissilePrototypeEquipped.Ability_Sequoia_Q_FragileMissilePrototypeEquipped_C",
        "/Game/Characters/Sequoia/S0/Ability_X/Ability_X_Sequoia_ArenaLineCapture.Ability_X_Sequoia_ArenaLineCapture_C",
        "/Game/Characters/Wraith/S0/Ability_4/Ability_Wraith_4_Smoke.Ability_Wraith_4_Smoke_C",
        "/Game/Characters/Wraith/S0/Ability_E/Ability_Wraith_E_ShortTeleport.Ability_Wraith_E_ShortTeleport_C",
        "/Game/Characters/Wraith/S0/Ability_Q/Ability_Wraith_Q_NearsightMissile.Ability_Wraith_Q_NearsightMissile_C",
        "/Game/Characters/Wraith/S0/Ability_X/Ability_Wraith_X_GlobalTeleport.Ability_Wraith_X_GlobalTeleport_C",
        "/Game/Characters/Rift/S0/Ability_4/BlackHole/Ability_Rift_4_BlackHole.Ability_Rift_4_BlackHole_C",
        "/Game/Characters/Rift/S0/Ability_Q/Ability_Rift_Q_FlashBurst.Ability_Rift_Q_FlashBurst_C",
        "/Game/Characters/Rift/S0/Ability_X/Ability_Rift_X_WorldTargetingStart.Ability_Rift_X_WorldTargetingStart_C",
        "/Game/Characters/Sarge/S0/Ability_Molotov/Ability_Sarge_Q_Molotov_Production.Ability_Sarge_Q_Molotov_Production_C",
        "/Game/Characters/Sarge/S0/Ability_SpeedStim/Ability_Sarge_E_SpeedStim.Ability_Sarge_E_SpeedStim_C",
        "/Game/Characters/Sarge/S0/Ability_OrbitalStrike/Ability_Sarge_X_OrbitalStrike.Ability_Sarge_X_OrbitalStrike_C",
        "/Game/Characters/AggroBot/S0/Ability_4/Ability_Aggrobot_C_ExplodeyPatch.Ability_Aggrobot_C_ExplodeyPatch_C",
        "/Game/Characters/AggroBot/S0/Ability_E/Ability_E_Aggrobot_DiscTurret.Ability_E_Aggrobot_DiscTurret_C",
        "/Game/Characters/AggroBot/S0/Ability_Q/Ability_Q_Aggrobot_SeekerNade.Ability_Q_Aggrobot_SeekerNade_C",
        "/Game/Characters/AggroBot/S0/Ability_X/Ability_Aggrobot_X_RollyExplosion.Ability_Aggrobot_X_RollyExplosion_C",
        "/Game/Characters/Pine/S0/Ability_4/Ability_Pine_4_UsableTP.Ability_Pine_4_UsableTP_C",
        "/Game/Characters/Pine/S0/Ability_E/Ability_Pine_E_RadEater.Ability_Pine_E_RadEater_C",
        "/Game/Characters/Pine/S0/Ability_Q/Ability_Pine_Q_SeizeTrap.Ability_Pine_Q_SeizeTrap_C",
        "/Game/Characters/Pine/S0/Ability_X/Ability_Pine_X_SelfBuffVers_Engineering.Ability_Pine_X_SelfBuffVers_Engineering_C",
        "/Game/Characters/Cashew/S0/Ability_4/Ability_Cashew_4_Spider_LockOn.Ability_Cashew_4_Spider_LockOn_C",
        "/Game/Characters/Cashew/S0/Ability_E/Ability_Cashew_E_Airstrike.Ability_Cashew_E_Airstrike_C",
        "/Game/Characters/Cashew/S0/Ability_Q/Ability_Cashew_Q_ShellShockGrenade.Ability_Cashew_Q_ShellShockGrenade_C",
        "/Game/Characters/Cashew/S0/Ability_X/Ability_Cashew_X_Airstrike.Ability_Cashew_X_Airstrike_C",
        "/Game/Characters/Terra/S0/Ability_4/Ability_Terra_C_TimeSlowGrenade.Ability_Terra_C_TimeSlowGrenade_C",
        "/Game/Characters/Terra/S0/Ability_E/Ability_Terra_E_RewindTime.Ability_Terra_E_RewindTime_C",
        "/Game/Characters/Terra/S0/Ability_Q/Ability_Terra_Q_DoubleDash.Ability_Terra_Q_DoubleDash_C",
        "/Game/Characters/Terra/S0/Ability_X/Ability_Terra_X_DelayedBeam.Ability_Terra_X_DelayedBeam_C",
        "/Game/Characters/Pandemic/S0/Ability_4/Ability_Pandemic_4_SmokeGrenade.Ability_Pandemic_4_SmokeGrenade_C",
        "/Game/Characters/Pandemic/S0/Ability_E/Ability_Pandemic_E_SmokeScreen.Ability_Pandemic_E_SmokeScreen_C",
        "/Game/Characters/Pandemic/S0/Ability_X/Ability_Pandemic_X_SmokeSite_New.Ability_Pandemic_X_SmokeSite_New_C",
    ];

    [Test]
    public void CatalogRegistersEveryPreviouslyMissingAbilityEquipmentPath()
    {
        var descriptors = ValorantDescriptors.CreateCatalog().ExportGroupDescriptors
            .ToDictionary(descriptor => descriptor.Path, StringComparer.Ordinal);

        Assert.Multiple(() =>
        {
            foreach (var path in MissingAbilityPaths)
            {
                if (!descriptors.TryGetValue(path, out var descriptor))
                {
                    Assert.Fail($"Missing descriptor: {path}");
                    continue;
                }

                Assert.That(descriptor.Fields, Is.Not.Empty, path);
                Assert.That(descriptor.Fields.All(field => field.Decoder is not null), Is.True, path);
            }
        });
    }

    [Test]
    public void AbilityCatalogPathsAreUnique()
    {
        var catalog = ValorantDescriptors.CreateCatalog();
        var duplicateExports = catalog.ExportGroupDescriptors
            .GroupBy(descriptor => descriptor.Path, StringComparer.Ordinal)
            .Where(group => group.Count() > 1)
            .Select(group => group.Key)
            .ToArray();
        var duplicateCaches = catalog.ClassNetCacheDescriptors
            .GroupBy(descriptor => descriptor.Path, StringComparer.Ordinal)
            .Where(group => group.Count() > 1)
            .Select(group => group.Key)
            .ToArray();

        Assert.Multiple(() =>
        {
            Assert.That(duplicateExports, Is.Empty);
            Assert.That(duplicateCaches, Is.Empty);
        });
    }

    [Test]
    public void IsoDescriptorsExposeMovingCoverSplineAndArenaPlacement()
    {
        var descriptors = ValorantDescriptors.CreateCatalog().ExportGroupDescriptors
            .ToDictionary(descriptor => descriptor.Path, StringComparer.Ordinal);

        var movingCover = descriptors[
            "/Game/Characters/Sequoia/S0/Ability_4/GameObject_Sequoia_4_MovingCover.GameObject_Sequoia_4_MovingCover_C"];
        var arena = descriptors[
            "/Game/Characters/Sequoia/S0/Ability_X/Actor_Sequoia_X_StandardArena.Actor_Sequoia_X_StandardArena_C"];
        var cover = descriptors[
            "/Game/Characters/Sequoia/S0/Ability_X/GameObject_Sequoia_X_ArenaCover.GameObject_Sequoia_X_ArenaCover_C"];
        var teleport = descriptors[SequoiaTeleportContextDescriptor.ExportPath];
        var spline = descriptors["/Script/ShooterGame.SplineMovementComponent"];

        Assert.Multiple(() =>
        {
            AssertFields(movingCover, "Owner", "Instigator");
            AssertFields(arena, "Owner", "AssignedPlayspace");
            AssertFields(cover, "bHidden", "AttachParent", "RelativeScale3D", "AttachComponent");
            Assert.That(teleport.Fields.Single(field => field.ExportName == "Translation").Decoder, Is.Not.Null);
            Assert.That(spline.Fields.Single(field => field.ExportName == "Trajectory").Decoder, Is.Not.Null);
        });
    }

    private static void AssertFields(ExportGroupDescriptor descriptor, params string[] names)
    {
        var actual = descriptor.Fields.Select(field => field.ExportName).ToHashSet(StringComparer.Ordinal);
        foreach (var name in names)
        {
            Assert.That(actual.Contains(name), Is.True, $"{descriptor.Path}:{name}");
        }
    }
}
