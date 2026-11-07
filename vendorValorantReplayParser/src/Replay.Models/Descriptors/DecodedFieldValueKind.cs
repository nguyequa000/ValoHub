namespace Replay.Models.Descriptors;

public enum DecodedFieldValueKind
{
    None,
    Bool,
    Byte,
    Int32,
    UInt32,
    UInt64,
    Float,
    Double,
    String,
    ObjectReference,
    Guid,
    Vector,
    Transform,
    Rotator,
    RepMovement,
    GameplayTag,
    Object,
}