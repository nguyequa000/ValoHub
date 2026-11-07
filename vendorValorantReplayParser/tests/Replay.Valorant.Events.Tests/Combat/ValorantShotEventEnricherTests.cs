using Replay.Encoding.Net;
using Replay.Models.Descriptors;
using Replay.Models.Events;
using Replay.Models.Net;
using Replay.Valorant.Combat;
using Replay.Valorant.Events.Combat;
using Replay.Valorant.GameState;

namespace Replay.Valorant.Events.Tests.Combat;

public class ValorantShotEventEnricherTests
{
    [Test]
    public void Emit_PlayerInventorySelectedGun_EnrichesShotImmediately()
    {
        var sink = new CapturingReplayEventSink();
        var enricher = new ValorantShotEventEnricher(sink, new NetGuidCache());

        enricher.Emit(new ActorSpawned(
            0, 0, 500, 0, false, null, 0, null,
            "/Game/Equippables/Guns/Rifles/AK/AssaultRifle_AK.AssaultRifle_AK_C",
            0, null, null, null, null));

        var playerState = new BombPlayerStateDescriptor { PossessedCharacter = new(20, null) };
        playerState.MarkDecoded(nameof(BombPlayerStateDescriptor.PossessedCharacter));
        enricher.Emit(ExportGroup(10, playerState));

        var inventory = new AresInventoryDescriptor { Character = new(20, null), CurrentEquippable = new(500, null) };
        inventory.MarkDecoded(nameof(AresInventoryDescriptor.Character));
        inventory.MarkDecoded(nameof(AresInventoryDescriptor.CurrentEquippable));
        enricher.Emit(ExportGroup(20, inventory));

        enricher.Emit(new ValorantShotReceived(1, 2, 30, 0, 4, new ValorantShot(
            null, null, null, null, null, null, null, null, null, null, null, null, null, null, null,
            new(10, null), null, [], new(500, null), null)));

        var shot = sink.Events.OfType<ValorantShotReceived>().Single().Shot;

        Assert.Multiple(() =>
        {
            Assert.That(shot.Equippable, Is.Not.Null);
            Assert.That(shot.Equippable!.NetGuid, Is.EqualTo(500));
            Assert.That(shot.Equippable.Name, Is.EqualTo("Vandal"));
            Assert.That(shot.Equippable.Category, Is.EqualTo(ValorantEquippableCategory.Rifle));
        });
    }

    [TestCase("/Game/Guns/Rifle/FiringState", ValorantShotFireMode.Primary)]
    [TestCase("/Game/Guns/Rifle/ZoomedFiringState", ValorantShotFireMode.Alternate)]
    [TestCase("/Game/Guns/Burst/FiringStateBurst", ValorantShotFireMode.Alternate)]
    public void Emit_FiringStatePath_EnrichesFireMode(string path, ValorantShotFireMode expected)
    {
        var sink = new CapturingReplayEventSink();
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(368, path);
        var enricher = new ValorantShotEventEnricher(sink, cache);

        enricher.Emit(Shot(firingState: new(368, null)));

        var shot = sink.Events.OfType<ValorantShotReceived>().Single().Shot;
        Assert.Multiple(() =>
        {
            Assert.That(shot.FireMode, Is.EqualTo(expected));
            Assert.That(shot.FireModeEvidence, Does.Contain(path));
        });
    }

    [Test]
    public void Emit_OuterAltFirePath_TakesPrecedenceOverGenericState()
    {
        var sink = new CapturingReplayEventSink();
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(368, "FiringState", new Models.Net.NetworkGuid(369));
        cache.SetNetGuidPath(369, "BasePistol_FXC_AltFire");
        var enricher = new ValorantShotEventEnricher(sink, cache);

        enricher.Emit(Shot(firingState: new(368, null)));

        Assert.That(sink.Events.OfType<ValorantShotReceived>().Single().Shot.FireMode,
            Is.EqualTo(ValorantShotFireMode.Alternate));
    }

    [Test]
    public void Emit_MissingFiringStatePath_LeavesFireModeUnknown()
    {
        var sink = new CapturingReplayEventSink();
        var enricher = new ValorantShotEventEnricher(sink, new NetGuidCache());

        enricher.Emit(Shot(firingState: new(368, null)));

        var shot = sink.Events.OfType<ValorantShotReceived>().Single().Shot;
        Assert.Multiple(() =>
        {
            Assert.That(shot.FireMode, Is.EqualTo(ValorantShotFireMode.Unknown));
            Assert.That(shot.FireModeEvidence, Is.Null);
        });
    }

    [Test]
    public void Emit_AltFireSource_EnrichesWithoutFiringStatePath()
    {
        var sink = new CapturingReplayEventSink();
        var enricher = new ValorantShotEventEnricher(sink, new NetGuidCache());

        enricher.Emit(Shot(sourceId: "FXC_BasePistol_AltFire"));

        Assert.That(sink.Events.OfType<ValorantShotReceived>().Single().Shot.FireMode,
            Is.EqualTo(ValorantShotFireMode.Alternate));
    }

    private static ValorantShotReceived Shot(NetObjectReference? firingState = null, string? sourceId = null) => new(
        1, 2, 30, 0, 4, new ValorantShot(
            null, null, sourceId, null, null, null, null, null, null, null, null, null, null, null, null,
            new(10, null), firingState, [], new(500, null), null));

    private static ExportGroupReceived ExportGroup(uint actorNetGuid, ExportGroupDescriptor payload) => new(
        0, 0, actorNetGuid, 0, 0, true, false, 0, payload.Path, payload.Kind, payload.Categories,
        0, 0, null, null, null, 0, 0, true, payload, 0, []);

    private sealed class CapturingReplayEventSink : IReplayEventSink
    {
        public List<ReplayEvent> Events { get; } = [];

        public void Emit(ReplayEvent replayEvent) => Events.Add(replayEvent);
    }
}
