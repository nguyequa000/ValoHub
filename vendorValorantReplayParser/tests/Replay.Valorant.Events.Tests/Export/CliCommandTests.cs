using System.CommandLine;
using CliReader;

namespace Replay.Valorant.Events.Tests.Export;

public class CliCommandTests
{
    [Test]
    public void RootCommand_NoArguments_WritesHelpAndSucceeds()
    {
        using var output = new StringWriter();
        using var error = new StringWriter();
        var configuration = new InvocationConfiguration
        {
            Output = output,
            Error = error,
        };

        var exitCode = CliCommandFactory.Create().Parse([]).Invoke(configuration);

        Assert.Multiple(() =>
        {
            Assert.That(exitCode, Is.Zero);
            Assert.That(output.ToString(), Does.Contain("[command] [options]"));
            Assert.That(output.ToString(), Does.Contain("log <replay-path>"));
            Assert.That(output.ToString(), Does.Contain("export <replay-path>"));
            Assert.That(error.ToString(), Is.Empty);
        });
    }

    [Test]
    public void ExportCommand_ValidArguments_SelectsExportCommand()
    {
        var replayPath = CreateReplayFile();
        try
        {
            var result = CliCommandFactory.Create().Parse(
                ["export", replayPath, "-o", "bundle", "-p", "viewer"]);

            Assert.Multiple(() =>
            {
                Assert.That(result.Errors, Is.Empty);
                Assert.That(result.CommandResult.Command.Name, Is.EqualTo("export"));
            });
        }
        finally
        {
            File.Delete(replayPath);
        }
    }

    [Test]
    public void ExportCommand_MissingOutput_ReturnsParseError()
    {
        var replayPath = CreateReplayFile();
        try
        {
            var result = CliCommandFactory.Create().Parse(["export", replayPath]);

            Assert.That(result.Errors, Is.Not.Empty);
        }
        finally
        {
            File.Delete(replayPath);
        }
    }

    [Test]
    public void ExportCommand_UnknownProfile_ReturnsParseError()
    {
        var replayPath = CreateReplayFile();
        try
        {
            var result = CliCommandFactory.Create().Parse(
                ["export", replayPath, "--output", "bundle", "--profile", "unknown"]);

            Assert.That(result.Errors, Is.Not.Empty);
        }
        finally
        {
            File.Delete(replayPath);
        }
    }

    [Test]
    public void LogCommand_MissingReplay_ReturnsParseError()
    {
        var result = CliCommandFactory.Create().Parse(["log", "missing.vrf"]);

        Assert.That(result.Errors, Is.Not.Empty);
    }

    private static string CreateReplayFile()
    {
        var path = Path.Combine(Path.GetTempPath(), $"valorant-cli-{Guid.NewGuid():N}.vrf");
        File.WriteAllBytes(path, []);
        return path;
    }
}
