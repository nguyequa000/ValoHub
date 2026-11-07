using System.Collections;
using System.Reflection;
using System.Text.Json;
using Replay.Models.Descriptors;
using Replay.Models.Net;
using Replay.Models.Unreal;

namespace CliReader.JsonExport;

internal sealed class ReplayJsonNormalizer
{
    internal const int MaxDepth = 12;
    internal const int MaxCollectionItems = 4096;

    public void WriteValue(Utf8JsonWriter writer, object? value)
    {
        WriteValue(writer, value, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
    }

    private void WriteValue(
        Utf8JsonWriter writer,
        object? value,
        int depth,
        HashSet<object> ancestors)
    {
        if (value is null || depth > MaxDepth)
        {
            writer.WriteNullValue();
            return;
        }

        if (TryWriteScalar(writer, value) || TryWriteUnrealValue(writer, value, depth, ancestors))
        {
            return;
        }

        if (!value.GetType().IsValueType && !ancestors.Add(value))
        {
            writer.WriteNullValue();
            return;
        }

        try
        {
            WriteComplexValue(writer, value, depth, ancestors);
        }
        finally
        {
            if (!value.GetType().IsValueType)
            {
                ancestors.Remove(value);
            }
        }
    }

    private static bool TryWriteScalar(Utf8JsonWriter writer, object value)
    {
        switch (value)
        {
            case string text:
                writer.WriteStringValue(text);
                return true;
            case bool boolean:
                writer.WriteBooleanValue(boolean);
                return true;
            case byte number:
                writer.WriteNumberValue(number);
                return true;
            case sbyte number:
                writer.WriteNumberValue(number);
                return true;
            case short number:
                writer.WriteNumberValue(number);
                return true;
            case ushort number:
                writer.WriteNumberValue(number);
                return true;
            case int number:
                writer.WriteNumberValue(number);
                return true;
            case uint number:
                writer.WriteNumberValue(number);
                return true;
            case long number:
                writer.WriteNumberValue(number);
                return true;
            case ulong number:
                writer.WriteNumberValue(number);
                return true;
            case float number:
                WriteFiniteNumber(writer, number);
                return true;
            case double number:
                WriteFiniteNumber(writer, number);
                return true;
            case decimal number:
                writer.WriteNumberValue(number);
                return true;
            case char character:
                writer.WriteStringValue(character.ToString());
                return true;
            case Enum enumValue:
                writer.WriteStringValue(ToSnakeCase(enumValue.ToString()));
                return true;
            case Guid guid:
                writer.WriteStringValue(guid);
                return true;
            case DateTime dateTime:
                writer.WriteStringValue(dateTime);
                return true;
            case DateTimeOffset dateTimeOffset:
                writer.WriteStringValue(dateTimeOffset);
                return true;
            case byte[] bytes:
                writer.WriteBase64StringValue(bytes);
                return true;
            case ReadOnlyMemory<byte> bytes:
                writer.WriteBase64StringValue(bytes.Span);
                return true;
            case Memory<byte> bytes:
                writer.WriteBase64StringValue(bytes.Span);
                return true;
            default:
                return false;
        }
    }

    private bool TryWriteUnrealValue(
        Utf8JsonWriter writer,
        object value,
        int depth,
        HashSet<object> ancestors)
    {
        switch (value)
        {
            case NetObjectReference reference:
                // Keep the stable viewer-export contract used by normalize.py:
                // object references are scalar NetGUIDs, while richer typed
                // domain objects (for example ValorantEquippable) remain objects.
                writer.WriteNumberValue(reference.NetGuid);
                return true;
            case FVector vector:
                WriteVector(writer, vector);
                return true;
            case FRotator rotator:
                WriteRotator(writer, rotator);
                return true;
            case FRepMovement movement:
                WriteRepMovement(writer, movement, depth, ancestors);
                return true;
            case DecodedFieldValue fieldValue:
                WriteDecodedFieldValue(writer, fieldValue, depth, ancestors);
                return true;
            default:
                return false;
        }
    }

    private void WriteComplexValue(
        Utf8JsonWriter writer,
        object value,
        int depth,
        HashSet<object> ancestors)
    {
        switch (value)
        {
            case IDictionary dictionary:
                WriteDictionary(writer, dictionary, depth, ancestors);
                break;
            case IEnumerable enumerable:
                WriteEnumerable(writer, enumerable, depth, ancestors);
                break;
            case IDecodedPayload payload:
                WriteObject(writer, value, PayloadProperties(value, payload), depth, ancestors);
                break;
            default:
                WriteObject(writer, value, PublicProperties(value), depth, ancestors);
                break;
        }
    }

    private void WriteDictionary(
        Utf8JsonWriter writer,
        IDictionary dictionary,
        int depth,
        HashSet<object> ancestors)
    {
        var entries = dictionary.Keys.Cast<object?>()
            .Select(key => (Name: key?.ToString() ?? "null", Value: key is null ? null : dictionary[key]))
            .OrderBy(entry => entry.Name, StringComparer.Ordinal)
            .Take(MaxCollectionItems);
        writer.WriteStartObject();
        foreach (var entry in entries)
        {
            writer.WritePropertyName(entry.Name);
            WriteValue(writer, entry.Value, depth + 1, ancestors);
        }

        writer.WriteEndObject();
    }

    private void WriteEnumerable(
        Utf8JsonWriter writer,
        IEnumerable enumerable,
        int depth,
        HashSet<object> ancestors)
    {
        writer.WriteStartArray();
        foreach (var item in enumerable.Cast<object?>().Take(MaxCollectionItems))
        {
            WriteValue(writer, item, depth + 1, ancestors);
        }

        writer.WriteEndArray();
    }

    private void WriteObject(
        Utf8JsonWriter writer,
        object value,
        IEnumerable<PropertyInfo> properties,
        int depth,
        HashSet<object> ancestors)
    {
        writer.WriteStartObject();
        foreach (var property in properties)
        {
            writer.WritePropertyName(property.Name);
            WriteValue(writer, ReadProperty(property, value), depth + 1, ancestors);
        }

        writer.WriteEndObject();
    }

    private static IEnumerable<PropertyInfo> PayloadProperties(object value, IDecodedPayload payload)
    {
        var type = value.GetType();
        return payload.DecodedProperties
            .OrderBy(name => name, StringComparer.Ordinal)
            .Select(name => type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public))
            .Where(property => property is not null && property.GetIndexParameters().Length == 0)!;
    }

    private static IEnumerable<PropertyInfo> PublicProperties(object value) =>
        value.GetType()
            .GetProperties(BindingFlags.Instance | BindingFlags.Public)
            .Where(property => property.CanRead && property.GetIndexParameters().Length == 0)
            .OrderBy(property => property.Name, StringComparer.Ordinal)
            .Take(MaxCollectionItems);

    private static object? ReadProperty(PropertyInfo property, object value)
    {
        try
        {
            return property.GetValue(value);
        }
        catch (Exception exception) when (exception is TargetInvocationException or NotSupportedException)
        {
            return null;
        }
    }

    private void WriteRepMovement(
        Utf8JsonWriter writer,
        FRepMovement movement,
        int depth,
        HashSet<object> ancestors)
    {
        writer.WriteStartObject();
        WriteProperty(writer, "linear_velocity", movement.LinearVelocity, depth, ancestors);
        WriteProperty(writer, "angular_velocity", movement.AngularVelocity, depth, ancestors);
        WriteProperty(writer, "location", movement.Location, depth, ancestors);
        WriteProperty(writer, "rotation", movement.Rotation, depth, ancestors);
        writer.WriteBoolean("simulated_physics_sleep", movement.bSimulatedPhysicsSleep);
        writer.WriteBoolean("rep_physics", movement.bRepPhysics);
        writer.WriteNumber("server_frame", movement.ServerFrame);
        writer.WriteNumber("server_physics_handle", movement.ServerPhysicsHandle);
        writer.WriteEndObject();
    }

    private void WriteDecodedFieldValue(
        Utf8JsonWriter writer,
        DecodedFieldValue value,
        int depth,
        HashSet<object> ancestors)
    {
        object? normalized = value.Kind switch
        {
            DecodedFieldValueKind.Bool => value.BoolValue,
            DecodedFieldValueKind.Byte => value.ByteValue,
            DecodedFieldValueKind.Int32 => value.Int32Value,
            DecodedFieldValueKind.UInt32 => value.UInt32Value,
            DecodedFieldValueKind.Float => value.FloatValue,
            DecodedFieldValueKind.Double => value.DoubleValue,
            DecodedFieldValueKind.String => value.StringValue,
            DecodedFieldValueKind.ObjectReference => value.ObjectReferenceValue,
            DecodedFieldValueKind.Vector => value.VectorValue,
            DecodedFieldValueKind.Rotator => value.RotatorValue,
            DecodedFieldValueKind.RepMovement => value.RepMovementValue,
            DecodedFieldValueKind.Object => value.ObjectValue,
            _ => null,
        };
        WriteValue(writer, normalized, depth + 1, ancestors);
    }

    private void WriteProperty(
        Utf8JsonWriter writer,
        string name,
        object? value,
        int depth,
        HashSet<object> ancestors)
    {
        writer.WritePropertyName(name);
        WriteValue(writer, value, depth + 1, ancestors);
    }

    internal static void WriteVector(Utf8JsonWriter writer, FVector vector)
    {
        writer.WriteStartObject();
        WriteFiniteNumber(writer, "x", vector.X);
        WriteFiniteNumber(writer, "y", vector.Y);
        WriteFiniteNumber(writer, "z", vector.Z);
        writer.WriteEndObject();
    }

    internal static void WriteRotator(Utf8JsonWriter writer, FRotator rotator)
    {
        writer.WriteStartObject();
        WriteFiniteNumber(writer, "pitch", rotator.Pitch);
        WriteFiniteNumber(writer, "yaw", rotator.Yaw);
        WriteFiniteNumber(writer, "roll", rotator.Roll);
        writer.WriteEndObject();
    }

    private static void WriteFiniteNumber(Utf8JsonWriter writer, float value)
    {
        if (float.IsFinite(value)) writer.WriteNumberValue(value);
        else writer.WriteNullValue();
    }

    private static void WriteFiniteNumber(Utf8JsonWriter writer, double value)
    {
        if (double.IsFinite(value)) writer.WriteNumberValue(value);
        else writer.WriteNullValue();
    }

    private static void WriteFiniteNumber(Utf8JsonWriter writer, string name, double value)
    {
        writer.WritePropertyName(name);
        WriteFiniteNumber(writer, value);
    }

    internal static string ToSnakeCase(string value)
    {
        return string.Concat(value.SelectMany((character, index) =>
            index > 0 && char.IsUpper(character)
                ? new[] { '_', char.ToLowerInvariant(character) }
                : new[] { char.ToLowerInvariant(character) }));
    }
}
