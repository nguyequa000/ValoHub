using System.CommandLine;
using System.CommandLine.Help;
using CliReader.JsonExport;

namespace CliReader;

internal static class CliCommandFactory
{
    public static RootCommand Create()
    {
        var root = new RootCommand(
            "Parse VALORANT replay files and either log decoded activity or export it as JSON.");
        root.Subcommands.Add(CreateLogCommand());
        root.Subcommands.Add(CreateExportCommand());
        root.SetAction(parseResult => new HelpAction().Invoke(parseResult));
        return root;
    }

    private static Command CreateLogCommand()
    {
        var replayPath = CreateReplayPathArgument();
        var command = new Command("log", "Log decoded replay activity and a parse summary.");
        command.Arguments.Add(replayPath);
        command.SetAction(parseResult =>
        {
            using var application = new CliApplication();
            return application.LogReplay(new LogOptions(parseResult.GetRequiredValue(replayPath).FullName));
        });
        return command;
    }

    private static Command CreateExportCommand()
    {
        var replayPath = CreateReplayPathArgument();
        var outputDirectory = new Option<DirectoryInfo>("--output", "-o")
        {
            Description = "Directory in which to create the JSON export bundle.",
            Required = true,
        };
        var profile = new Option<string>("--profile", "-p")
        {
            Description = "Parse profile: 'default' or 'viewer'.",
            DefaultValueFactory = _ => "default",
        };
        profile.AcceptOnlyFromAmong("default", "viewer");

        var command = new Command("export", "Export replay events and movement as versioned NDJSON.");
        command.Arguments.Add(replayPath);
        command.Options.Add(outputDirectory);
        command.Options.Add(profile);
        command.SetAction(parseResult =>
        {
            var profileName = parseResult.GetRequiredValue(profile);
            var options = ExportOptions.Create(
                parseResult.GetRequiredValue(replayPath).FullName,
                parseResult.GetRequiredValue(outputDirectory).FullName,
                profileName);
            using var application = new CliApplication();
            return application.ExportReplay(options);
        });
        return command;
    }

    private static Argument<FileInfo> CreateReplayPathArgument()
    {
        var argument = new Argument<FileInfo>("replay-path")
        {
            Description = "Path to the VALORANT replay file.",
        };
        argument.AcceptExistingOnly();
        return argument;
    }
}
