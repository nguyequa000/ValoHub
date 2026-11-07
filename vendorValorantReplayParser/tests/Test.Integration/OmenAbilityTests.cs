using Microsoft.Extensions.Logging.Abstractions;
using NSubstitute;
using Replay.Encoding.Archives;
using Replay.Models.Descriptors;
using Replay.Models.Events;
using Replay.Valorant;
using Replay.Valorant.Descriptors.Agents.Wraith;

namespace Test.Integration;

[Category("Integration")]
public class OmenAbilityTests
{
    private class Sink : IReplayEventSink
    {
        private Action<DarkCoverAbilityDescriptor> CoveConsumer { get; set; }
        
        public Sink(Action<DarkCoverAbilityDescriptor> coveConsumer)
        {
            CoveConsumer = coveConsumer;
        }

        public void Emit(ReplayEvent replayEvent)
        {
            switch (replayEvent)
            {
                case ExportGroupReceived export:
                    if (export.Payload is DarkCoverAbilityDescriptor cove)
                    {
                        CoveConsumer(cove);
                    }
                    break;
            }
        }
    }

    [Test]
    public void TestDarkCover()
    {
        var archive = new FBinaryArchive(TestHelper.ReadReplayBytes("omen.vrf"));

        var coveConsumer = Substitute.For<Action<DarkCoverAbilityDescriptor>>();
        var parseNothingProfile = new ParseProfile
        {
            EnabledCategories = ExportCategory.Ability,
        };
        var eventSink = new Sink(coveConsumer);

        _ = ValorantReplayReader.CreateDefault(new NullLoggerFactory(), eventSink, parseNothingProfile).Read(archive);
        Assert.Multiple(() =>
        {
            Assert.That(coveConsumer.ReceivedCalls().All(_ => _.GetArguments().Cast<DarkCoverAbilityDescriptor>().First().Instigator?.NetGuid is 134 or null), Is.True);
            Assert.That(coveConsumer.ReceivedCalls().Select(_ => _.GetArguments().Cast<DarkCoverAbilityDescriptor>().First().ReplicatedMovement).Count(_ => _ is not null), Is.EqualTo(1074));
            Assert.That(coveConsumer.ReceivedCalls().Select(_ => _.GetArguments().Cast<DarkCoverAbilityDescriptor>().First().ReplicatedMovement).Count(_ => _ is null), Is.EqualTo(2));
        });
    }
}