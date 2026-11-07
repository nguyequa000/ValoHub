using Replay.Models.Net;
using Replay.Models.Unreal;
using Replay.Valorant.Descriptors.Effects.Replay;
using Replay.Valorant.Events.Combat;

namespace Replay.Valorant.Events.Tests.Combat;

public class ValorantShotFactoryTests
{
    [Test]
    public void CreateShot_MapsOnlyNamedFiringStateValues()
    {
        var attackVector1 = new FVector(1, 0, 0);
        var attackVector2 = new FVector(0, 1, 0);
        var attackVector10 = new FVector(0, 0, 1);
        var firingState = new NetObjectReference(368, "PrimaryFiringState");
        var effectContainer = new NetObjectReference(17, "/Game/Effects/Fire.Fire_C");
        var parameters = new ReplayPlayContinuousEffectAtLocationParameters
        {
            EffectContainer = effectContainer,
            EffectId = 4,
            StartMovementTime = 7.3303437f,
            FloatValues =
            [
                FloatValue("FiringState.NumProjectiles", 1),
                FloatValue("FiringState.AmmoRemaining", 11),
                FloatValue("FiringState.TracerOption", 1),
                FloatValue("FiringState.RandomSeed", -894045900),
                FloatValue("FXC.Duration", 99),
                new EffectDataFloat { Name = new FGameplayTag(65535, null), Float = 123 },
            ],
            VectorValues =
            [
                VectorValue("FiringState.AttackVector.10", attackVector10),
                VectorValue("FiringState.AttackVector.2", attackVector2),
                VectorValue("FiringState.AttackVector.1", attackVector1),
                VectorValue("FiringState.AttackVector.16", new FVector(16, 0, 0)),
            ],
            ObjectValues =
            [
                new EffectDataObject { Name = new FGameplayTag(0, "FiringState.FiringState"), Object = firingState },
                ObjectValue("FiringState.FiringPlayerState", 92),
                ObjectValue("FXC.EffectContext", 146),
                ObjectValue("FXC.Equippable", 500),
            ],
        };

        var shot = ValorantShotFactory.Create(parameters);

        Assert.Multiple(() =>
        {
            Assert.That(shot.EffectId, Is.EqualTo(4));
            Assert.That(shot.StartMovementTime, Is.EqualTo(7.3303437f));
            Assert.That(shot.NumProjectiles, Is.EqualTo(1));
            Assert.That(shot.AmmoRemaining, Is.EqualTo(11));
            Assert.That(shot.TracerOption, Is.EqualTo(1));
            Assert.That(shot.RandomSeed, Is.EqualTo(-894045900));
            Assert.That(shot.FiringState, Is.EqualTo(firingState));
            Assert.That(shot.EffectContainer, Is.EqualTo(effectContainer));
            Assert.That(shot.FiringPlayerState?.NetGuid, Is.EqualTo(92));
            Assert.That(shot.EffectEquippable?.NetGuid, Is.EqualTo(500));
            Assert.That(shot.AttackVectors, Is.EqualTo(new[] { attackVector1, attackVector2, attackVector10 }));
        });
    }

    private static EffectDataFloat FloatValue(string tag, float value) => new() { Name = new FGameplayTag(0, tag), Float = value };
    private static EffectDataVector VectorValue(string tag, FVector value) => new() { Name = new FGameplayTag(0, tag), Vector = value };
    private static EffectDataObject ObjectValue(string tag, uint value) => new() { Name = new FGameplayTag(0, tag), Object = new NetObjectReference(value, null) };
}
