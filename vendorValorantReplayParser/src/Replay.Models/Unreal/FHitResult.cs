namespace Replay.Models.Unreal;

public sealed record FHitResult(
    bool BlockingHit, bool StartPenetrating, float Time,
    FVector Location, FVector Normal, FVector ImpactPoint, FVector ImpactNormal,
    FVector TraceStart, FVector TraceEnd, float PenetrationDepth, int Item,
    uint PhysMaterial, uint Actor, uint Manager, int InstanceIndex, uint Component,
    string BoneName, int FaceIndex, byte ElementIndex);
