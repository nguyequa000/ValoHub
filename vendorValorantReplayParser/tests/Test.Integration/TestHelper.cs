namespace Test.Integration;

public static class TestHelper
{
    public static byte[] ReadReplayBytes(string replayFileName)
    {
        var path = Path.Combine(TestContext.CurrentContext.TestDirectory, "Replays", replayFileName);
        return File.ReadAllBytes(path);
    }
}