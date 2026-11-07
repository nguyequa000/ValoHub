using Replay.Models.Descriptors;

namespace CliReader.JsonExport;

internal sealed record ExportOptions(
    string ReplayPath,
    string OutputDirectory,
    string ProfileName,
    ParseProfile ParseProfile)
{
    public static ExportOptions Create(
        string replayPath,
        string outputDirectory,
        string profileName)
    {
        var parseProfile = profileName switch
        {
            "default" => ParseProfile.Default,
            "viewer" => new ParseProfile
            {
                EnabledCategories = ExportCategory.All,
                CaptureDiagnosticFields = true,
            },
            _ => throw new ArgumentOutOfRangeException(
                nameof(profileName),
                profileName,
                "Export profile must be 'default' or 'viewer'."),
        };
        return new ExportOptions(replayPath, outputDirectory, profileName, parseProfile);
    }
}
