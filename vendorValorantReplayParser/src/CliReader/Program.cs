namespace CliReader;

internal static class Program
{
    public static int Main(string[] args) => CliCommandFactory.Create().Parse(args).Invoke();
}
