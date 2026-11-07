using Replay.Encoding.Net;
using Replay.Valorant.Descriptors.Agents;
using Replay.Valorant.GameState;

namespace Replay.Valorant.Events;

internal sealed class PlayerIdentityTracker(NetGuidCache netGuidCache)
{
    private const int MaxOuterDepth = 16;
    private readonly NetGuidCache _netGuidCache = netGuidCache;
    private readonly Dictionary<uint, PlayerIdentity> _playersByState = [];
    private readonly Dictionary<uint, uint> _playerStateByCharacter = [];
    private readonly HashSet<uint> _playerCharacters = [];

    public bool IsPlayerCharacter(uint characterNetGuid) => _playerCharacters.Contains(characterNetGuid);

    public void TrackAgent(uint characterNetGuid, GenericAgentDescriptor agent)
    {
        _playerCharacters.Add(characterNetGuid);
        if (agent.HasDecoded(nameof(GenericAgentDescriptor.PlayerState)) && agent.PlayerState.NetGuid != 0)
        {
            SetCharacterPlayerState(characterNetGuid, agent.PlayerState.NetGuid);
        }
    }

    public void TrackPlayerState(uint playerStateNetGuid, BombPlayerStateDescriptor playerState)
    {
        if (!_playersByState.TryGetValue(playerStateNetGuid, out var identity))
        {
            identity = new PlayerIdentity();
            _playersByState.Add(playerStateNetGuid, identity);
        }

        if (playerState.HasDecoded(nameof(BombPlayerStateDescriptor.Subject)))
        {
            identity.Subject = playerState.Subject;
        }

        if (playerState.HasDecoded(nameof(BombPlayerStateDescriptor.PossessedCharacter)))
        {
            SetCharacterPlayerState(playerState.PossessedCharacter.NetGuid, playerStateNetGuid);
        }
        // Possession establishes ownership, not an agent body: cameras and drones
        // also appear as PossessedCharacter. Process SpawnedCharacter independently.
        if (playerState.HasDecoded(nameof(BombPlayerStateDescriptor.SpawnedCharacter)) &&
            playerState.SpawnedCharacter.NetGuid != 0)
        {
            _playerCharacters.Add(playerState.SpawnedCharacter.NetGuid);
            SetCharacterPlayerState(playerState.SpawnedCharacter.NetGuid, playerStateNetGuid);
        }
    }

    private void SetCharacterPlayerState(uint characterNetGuid, uint playerStateNetGuid)
    {
        if (characterNetGuid == 0 || playerStateNetGuid == 0)
        {
            return;
        }

        _playerStateByCharacter[characterNetGuid] = playerStateNetGuid;
    }

    public uint? ResolveCaster(uint? ownerNetGuid, uint? instigatorNetGuid)
    {
        if (instigatorNetGuid is > 0)
        {
            return instigatorNetGuid;
        }

        if (ownerNetGuid is not > 0)
        {
            return null;
        }

        var current = ownerNetGuid.Value;
        for (var depth = 0; depth < MaxOuterDepth; depth++)
        {
            if (_playerCharacters.Contains(current) || _playerStateByCharacter.ContainsKey(current))
            {
                return current;
            }

            if (!_netGuidCache.TryGetOuterNetGuid(current, out var outer))
            {
                break;
            }

            current = outer.Value;
        }

        return ownerNetGuid;
    }

    public uint? GetPlayerState(uint? characterNetGuid) =>
        characterNetGuid is { } character && _playerStateByCharacter.TryGetValue(character, out var playerState)
            ? playerState
            : null;

    public string? GetSubject(uint? playerStateNetGuid) =>
        playerStateNetGuid is { } playerState && _playersByState.TryGetValue(playerState, out var identity)
            ? identity.Subject
            : null;

    private sealed class PlayerIdentity
    {
        public string? Subject { get; set; }
    }
}
