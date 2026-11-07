"""Stdlib container/index reader for ``.vrf`` replays — no packet decoder needed.

``index_replay(path)`` reads the Unreal replay container (round starts, deaths,
plants, defuses, ultimates, side switch) straight from the chunk index and
returns plain dicts. This is the surviving logic of the retired root extractor
(P3/P0-D, 2026-10-13 — see ``VALOPLANT_WORK/DELETED.md``): the base64 chunk
dumps, output files and the optional .NET decoder call are gone. Container
parsing itself lives in ``bot.replay.pipeline`` (``peek_container``,
``read_container_prefix``, ``Reader``) and is imported, never duplicated.

The index is deliberately partial and says so: it carries event times and group
ids, but no player identities, no coordinates, and no round winners except
defuse rounds — a defuse is a defender win. It works on patch 11.07 and 13.06
alike because it never touches movement packets, so an 11.07 replay whose packet
decode fails is still indexable.
"""

from __future__ import annotations

import math
import struct
from collections import Counter
from pathlib import Path

from .pipeline import Reader, peek_container, read_container_prefix

MAX_CHUNK_BYTES = 1 << 26       # same "corrupt chunk table" guard as peek_container
PREFIX_READ_BYTES = 1 << 20     # the container prefix always fits in the first MiB

# Validated indexed-event payload layouts: group -> (event code, field count, enum label).
# A payload that does not match exactly is reported undecoded, never guessed.
EVENT_LAYOUTS = {
    "roundStarted": (2, 1, "RoundStart"),
    "switchTeams": (3, 1, "SwitchTeams"),
    "spikePlanted": (4, 0, "SpikePlanted"),
    "spikeDefused": (5, 0, "SpikeDefused"),
    "characterDeath": (8, 2, "CharacterDeath"),
    "characterUltimateUsed": (11, 1, "CharacterUltimateUsed"),
}


def _event_record(payload: bytes, offset: int) -> dict:
    """Decode one indexed-event chunk: envelope always, known payloads when validated."""
    r = Reader(payload)
    record = {
        "offset": offset,
        "id": r.string(),
        "group": r.string(),
        "metadata": r.string(),
        "timeMs": r.number(),
        "endTimeMs": r.number(),
    }
    body = r.take(r.number())
    if r.pos != len(payload):
        raise ValueError(f"event chunk at {offset} has trailing bytes")
    record["payloadStatus"] = "unknownEventGroup"
    layout = EVENT_LAYOUTS.get(record["group"])
    if layout is None:
        return record
    code, count, label = layout
    reader = Reader(body)
    try:
        actual = reader.number()
        fields = [reader.number() for _ in range(count)]
        enum = reader.string()
        seconds = reader.number("f")
        if (actual != code or enum != "EReplayEventGroup::" + label
                or reader.pos != len(body) or not math.isfinite(seconds)
                or abs(seconds * 1000 - record["timeMs"]) > 5):
            raise ValueError("payload does not match the validated index-event layout")
        record.update(eventCode=actual, enum=enum, payloadTimeSeconds=seconds)
        record["payloadStatus"] = "decoded"
        if record["group"] in ("roundStarted", "switchTeams"):
            record["roundIndex"] = fields[0]
        else:
            # Integer references, not verified killer/victim/player ids — no attribution.
            record["referenceIds"] = fields
    except (ValueError, UnicodeError, struct.error) as exc:
        record["payloadStatus"] = "unknownLayout"
        record["payloadError"] = str(exc)
    return record


def _read_events(stream, prefix_end: int) -> list[dict]:
    """Walk the chunk table after the prefix, decoding every kind-3 event chunk."""
    events: list[dict] = []
    offset = prefix_end
    while True:
        stream.seek(offset)
        head = stream.read(8)
        if not head:
            break
        if len(head) < 8:
            raise ValueError(f"truncated chunk header at {offset}")
        kind, size = struct.unpack("<Ii", head)
        if size < 0 or size > MAX_CHUNK_BYTES:
            raise ValueError("corrupt chunk table")
        if kind == 3:
            payload = stream.read(size)
            if len(payload) != size:
                raise ValueError(f"truncated event chunk at {offset}")
            events.append(_event_record(payload, offset))
        offset += 8 + size
    return events


def _summarize_rounds(events: list[dict], duration_ms: int) -> list[dict]:
    """Group indexed events into round intervals, oldest start first.

    A round interval runs from its indexed start to the next start (the last one
    to the replay duration) and therefore includes pre-round time — it is not a
    live-phase duration. Deaths, plants and defuses inside the interval are the
    only outcome evidence the index has.
    """
    starts = sorted((e for e in events if e["group"] == "roundStarted"
                     and e["payloadStatus"] == "decoded"), key=lambda e: e["timeMs"])
    if len({e["roundIndex"] for e in starts}) != len(starts):
        raise ValueError("duplicate round indexes; cannot safely summarize rounds")
    rounds: list[dict] = []
    for index, start in enumerate(starts):
        end = starts[index + 1]["timeMs"] if index + 1 < len(starts) else duration_ms + 1
        rows = [e for e in events if start["timeMs"] <= e["timeMs"] < end]
        deaths = sorted((e for e in rows if e["group"] == "characterDeath"),
                        key=lambda e: e["timeMs"])
        defuses = [e["timeMs"] for e in rows if e["group"] == "spikeDefused"]
        rounds.append({
            "roundIndex": start["roundIndex"],
            "roundNumber": start["roundIndex"] + 1,
            "startMs": start["timeMs"],
            "endMs": min(end, duration_ms),
            "intervalEndIsRoundEnd": False,
            "deaths": [{"timeMs": e["timeMs"], "eventId": e["id"],
                        "referenceIds": e.get("referenceIds", [])} for e in deaths],
            "deathCount": len(deaths),
            "plantTimesMs": [e["timeMs"] for e in rows if e["group"] == "spikePlanted"],
            "defuseTimesMs": defuses,
            "ultimateCount": sum(e["group"] == "characterUltimateUsed" for e in rows),
            "defenderWin": bool(defuses),
        })
    return rounds


def index_replay(path) -> dict:
    """Index one ``.vrf`` file in place. Read-only, stdlib only, never writes.

    Returns ``{"path", "container", "eventCounts", "undecodedPayloadCount",
    "rounds", "ultimateTimesMs", "sideSwitchTimesMs"}``:

    - ``container`` — ``peek_container`` output (containerVersion, durationMs,
      branch, mapPath);
    - ``eventCounts`` — indexed events per group (whole file);
    - ``rounds`` — one entry per indexed round start, with ``startMs``/``endMs``,
      ``deaths``, ``plantTimesMs``, ``defuseTimesMs`` and ``defenderWin``
      (a defuse is a defender win; every other outcome is unknown);
    - ``ultimateTimesMs`` / ``sideSwitchTimesMs`` — event times, oldest first.

    Raises ValueError when the file is not a replay container, or OSError when
    it cannot be read.
    """
    path = Path(path)
    container = peek_container(path)
    with path.open("rb") as stream:
        reader = Reader(stream.read(PREFIX_READ_BYTES))
        read_container_prefix(reader)
        events = _read_events(stream, reader.pos)

    duration_ms = int(container.get("durationMs") or 0)
    counts = Counter(e["group"] for e in events)
    return {
        "path": str(path),
        "container": container,
        "eventCounts": dict(counts),
        "undecodedPayloadCount": sum(e["payloadStatus"] != "decoded" for e in events),
        "rounds": _summarize_rounds(events, duration_ms),
        "ultimateTimesMs": sorted(e["timeMs"] for e in events
                                  if e["group"] == "characterUltimateUsed"),
        "sideSwitchTimesMs": sorted(e["timeMs"] for e in events
                                    if e["group"] == "switchTeams"),
    }
