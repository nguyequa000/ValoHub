using System.Text.Json;

namespace CliReader.JsonExport;

internal sealed class NdjsonWriter : IDisposable
{
    private readonly Stream _stream;

    public NdjsonWriter(Stream stream)
    {
        _stream = stream;
    }

    public static NdjsonWriter Create(string path)
    {
        var stream = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
        return new NdjsonWriter(stream);
    }

    public void Write(Action<Utf8JsonWriter> writeValue)
    {
        using (var writer = new Utf8JsonWriter(_stream))
        {
            writeValue(writer);
            writer.Flush();
        }

        _stream.WriteByte((byte)'\n');
    }

    public void Dispose() => _stream.Dispose();
}
