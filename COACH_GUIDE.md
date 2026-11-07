# COACH_GUIDE.md — Self-Review Coach Rubric & 9B Prompt

Adapts the analyst rubric that predates this repository's replay work into the system prompt for
the **offline personal coach**: a tool that
reads one player's own replay store (`bot/replay/coach_metrics.py`) and tells that player what
they did wrong, why it cost the round, and what drill fixes it. It does not scout opponents and
it does not touch the network — everything it says must trace to a `coach_metrics` finding with
a resolvable `(round, t)` ref (the P4-G grounding gate).

Finding kinds referenced below are the exact `kind` strings `coach_metrics.build_findings()`
emits (grep hits, `bot/replay/coach_metrics.py`):

```
first_death      line 820
first_blood      line 828
untraded_death   line 840
death_cluster    line 853
util_timing      line 874, 882
post_plant       line 898
survival_side    line 919
loss_involvement line 935
```

---

## 1. T1 phase/step → offline-derivable or out of scope

| T1 phase / step | Offline from a replay? | `coach_metrics` finding kind | Why |
|---|---|---|---|
| Phase 1 — Enemy Map Pool Intelligence (vdc.gg pick/ban/veto history) | **OUT OF SCOPE** | — | Needs vdc.gg; this coach only has your own replay store, and this is opponent scouting, not self-review. |
| Phase 1 — Correlated Map identification | **OUT OF SCOPE** | — | Depends on Phase 1 web data. |
| Phase 2 — Player Profiling (tracker.gg rank/ACS/HS%/agent pool) | **OUT OF SCOPE** | — | Needs tracker.gg; not present in the replay store schema. |
| Phase 2.2 — Custom-game round patterns | **OUT OF SCOPE** | — | Needs tracker.gg match history; the replay store holds no cross-match aggregation. |
| Phase 3.1 — Attack: who dies first / who gets the opening kill | derivable | `first_death`, `first_blood` | Per-round first-kill/first-death event, with attack/defense split. |
| Phase 3.1 — Attack: entry trade follow-up | derivable | `untraded_death` | 5.0 s trade window re-implemented from `first_contact_analyzer`. |
| Phase 3.1 — Attack: utility usage timing vs. default/fast execute | derivable | `util_timing` | Median utility time vs. round elapsed and vs. first contact. |
| Phase 3.1 — Attack: eco/save round buy pattern | **OUT OF SCOPE** | — | No economy/buy events exist in the viewer schema (`event.type` is only `kill`/`plant`/`defuse`/`ult`/`ability`). |
| Phase 3.1 — Attack: post-plant setups / holds | derivable | `post_plant` | Alive-at-plant state and distance to the spike while alive. |
| Phase 3.2 — Defense: anchor/retake positions | partially derivable | `death_cluster`, `survival_side` | Death location (32 px grid / 128 px region) and survival rate are known; **named sites/callouts are out of scope** — `spike.site` is an unproven enum byte, never surfaced as a site name. |
| Phase 3.2 — Defense: who roams vs. who anchors | **OUT OF SCOPE** | — | Requires named positions/roles the store does not carry. |
| Phase 3.3 — Recurring patterns & tendencies (5+) | derivable (aggregate) | `death_cluster`, `survival_side`, `util_timing`, `loss_involvement` | A "pattern" for self-review is a cluster of same-kind findings across rounds, not a new metric. |
| Phase 4 — Stream/social/VOD OSINT (all of it) | **OUT OF SCOPE** | — | Public internet research; has no replay-store equivalent and is explicitly opponent-facing. |
| Phase 5 — Executive Summary | derivable | all kinds, aggregated | Built from the player's own `findings` list (sorted by severity). |
| Phase 5 — Key Threats table (enemy players to neutralize) | **rewritten, not out of scope** | `first_death`, `untraded_death`, `loss_involvement` | Self-review equivalent is "Top 3 Mistakes" below — the threat is your own highest-severity findings, not an opponent roster. |
| Phase 5 — Attack/Defense Counter-Strats | **rewritten, not out of scope** | all kinds → drills | Self-review equivalent is the Drills list (§2) — a drill per mistake kind instead of a counter per opponent tendency. |
| Phase 5 — Suggested Agent Composition | **OUT OF SCOPE** | — | Team-comp/meta decision; not inferable from one player's metrics. |

Everything marked derivable maps to a `coach_metrics.py` finding kind that exists today; nothing
in §1 or §2 invents a metric the module does not compute.

---

## 2. Self-review rubric (replaces T1 Phase 3 checklist + Phase 5 report)

This is "what did I do wrong," not "what does the enemy do." Every row below is produced from
one player's `player_report()` output and must cite the `refs` already attached to the finding —
never invent a round or timestamp.

### 2.1 Attack self-review (was T1 Step 3.1)

| Question | Finding kind | Read it as |
|---|---|---|
| Did I die first this round, and on which side? | `first_death` | Early death on attack with no trade = lost the round's info/tempo before the rest of the team engaged. |
| Did I get the opening pick instead? | `first_blood` | Credit when it happens; if it rarely happens, entries are being given away. |
| When I died, did a teammate avenge me within 5 s? | `untraded_death` | Untraded = died for nothing — team gets no numbers advantage back. |
| When did I use my utility relative to first contact? | `util_timing` | Utility thrown after the first death already happened is wasted — it should land before or at entry, not as a reaction. |
| Was I alive when the spike was planted, and how far from it? | `post_plant` | Dying immediately post-plant, or holding too far to contest a defuse, both lose plants that should convert. |

**Self-review write-up per mistake:** *what happened → why it cost the round → the drill.*
Example shape (not a template to fill with invented numbers — pull the real values from the
finding `text` and `refs`):

> Mistake: first death on 6 of 11 attack rounds (55%), rank 9/10 (lower is better).
> Why it cost the round: dying before any trade info is shared gives the team zero read on the
> defense setup for that round — four of those six rounds were then lost.
> Drill: 2-man default entry drill — hold the angle one beat longer, let a flash or smoke land
> before peeking, don't isolate the first contact.

### 2.2 Defense self-review (was T1 Step 3.2)

| Question | Finding kind | Read it as |
|---|---|---|
| Where do I keep dying on defense? | `death_cluster` | A repeated grid cell/region (not a named site — the store can't name sites) means a predictable hold the enemy can pre-aim or flank. |
| Am I surviving defense rounds less often than attack rounds? | `survival_side` | A side imbalance in survival rate points at a positioning habit specific to that side, not just bad luck. |
| Do I keep dying in rounds my team loses, and before the round is even decided? | `loss_involvement` | Dying early in a round that's later lost (before the defuse/final kill) usually means overextending past the point the round was recoverable. |

**Cross-check (not a finding kind):** when reviewing throw count/mix, consult the analysis page's
**Per-player utility** table (a display of `match.utilityCounts`, not a `coach_metrics` finding) —
it lists each player's total utility thrown and top abilities for the match.

### 2.3 Phase 5 self-review report format (was T1 Phase 5)

1. **Executive Summary** — 3–5 sentences, generated from the sorted `findings` list
   (`SEVERITY_ORDER`: major → minor → info): what is the single biggest recurring mistake this
   match, and roughly how often does it happen.
2. **Top 3 Mistakes** — the three highest-severity findings (ties broken by `kind`, then `id`,
   same order `build_findings()` already sorts in). For each: the finding `text` verbatim-grounded,
   *why it cost the round*, and its `refs`.
3. **Drills** — one drill per mistake in the Top 3, keyed to that finding's `kind` (see mapping
   in §2.1/§2.2 — e.g. `untraded_death` → trade-discipline drill, `death_cluster` → off-angle/
   position-rotation drill, `util_timing` → pre-round utility-timing drill, `post_plant` →
   post-plant hold drill, `survival_side`/`loss_involvement` → side-specific patience/rotation
   drill).
4. **Refs** — every ref cited anywhere in the report, unioned; each one must already appear in
   the `coach_metrics` findings handed to the report — never a round/time the coach made up.

No "Suggested Agent Composition" section — out of scope per §1.

---

## 3. System prompt — 9B local model (Qwen3.8 9B)

Use for the optional LLM-narration leg of the coach API (P4-B), on top of the rules-first
findings `coach_metrics.build_findings()` already produced. The model only rewrites/ranks
existing findings into prose — it does not compute new metrics and must not cite anything not
handed to it. This is the prompt and schema actually shipped in `bot/replay/p3_coach.py`
(`SYSTEM_PROMPT`, `build_messages`, `parse_narration`, `validate_narration`) — quoted verbatim
below, not a redesign.

`build_messages()` sends `SYSTEM_PROMPT` as the `system` message and a `user` message containing
the findings JSON: `{"player", "map", "roundsAnalyzed", "findings": [{id, kind, severity, text,
refs}, ...]}`.

```
SYSTEM_PROMPT (verbatim, bot/replay/p3_coach.py):

You are a Valorant coach writing a short personal review for one player from
their deterministic replay findings. Narrate THEM only; never invent events,
numbers or timings.
Every mistake and every drill MUST cite at least one ref copied verbatim from
the "refs" lists below (each ref looks like {"round": <int>, "t": <ms>}); an
item citing anything not present there is discarded.
Reply with ONLY a JSON object: {"summary": "<2-4 sentences>", "mistakes":
[{"title": "<short>", "detail": "<what to do differently>", "severity":
"major|minor|info", "refs": [{"round": 5, "t": 48200}]}], "drills": [{"title":
"<short>", "detail": "<concrete practice drill>",
"refs": [{"round": 5, "t": 48200}]}]}
Order mistakes by importance; at most 3 mistakes.
```

OUTPUT schema — what `parse_narration`/`validate_narration` actually accept (top-level keys are
exactly `summary`, `mistakes`, `drills`; there is no `top3Mistakes`, no top-level `refs`, no
`rank`/`kind`/`whyItCost`/`forMistake` field anywhere):

```
{
  "summary": "<2-4 sentences: the single biggest recurring mistake this match and how often>",
  "mistakes": [
    {
      "title": "<short>",
      "detail": "<what to do differently>",
      "severity": "major|minor|info",
      "refs": [ {"round": <int>, "t": <int ms>} ]   // copied verbatim from the input findings
    }
    // ordered by importance; at most 3 are kept (MAX_MISTAKES)
  ],
  "drills": [
    {
      "title": "<short>",
      "detail": "<concrete practice drill>",
      "refs": [ {"round": <int>, "t": <int ms>} ]
    }
    // flat list, NOT keyed to a mistake rank; at most 6 are kept (MAX_DRILLS)
  ]
}
```

RULES (enforced by `parse_narration`/`validate_narration`, not just asked of the model):
- `refs` are `{"round": <int>, "t": <int ms>}` pairs only — never fabricate a round, timestamp,
  or event.
- Grounding is checked against the rules findings' own `refs`, not just asserted by the model: a
  mistake or drill with no ref matching one already present in the findings handed to the model
  is dropped entirely; a surviving item keeps only the refs that matched.
- `mistakes` is capped at `MAX_MISTAKES = 3` after grounding; `drills` is capped at
  `MAX_DRILLS = 6`. There is no required drill-per-mistake pairing or count — `drills` is a flat
  list, independent of how many mistakes survive.
- `severity` must be `major`, `minor`, or `info`; anything else is normalized to `info`.
- A missing or empty `summary` makes the whole reply unusable (treated as unparseable) — the
  caller sends `NARRATION_RETRY` once, then falls back to `llm.available=false` with a reason;
  this is not a hard error, the rules findings still return as the payload.
- Never mention an opponent, an enemy player, or a map veto — this is self-review only.
- Never name a map site or callout (e.g. "A site", "B main") — the store has no site/callout
  names to cite.

Retry prompt sent verbatim when the first reply does not parse (`NARRATION_RETRY`,
`bot/replay/p3_coach.py`):

```
Your previous reply could not be parsed. Reply with ONLY a single JSON object,
no prose, no markdown fences, exactly:
{"summary": "<string>", "mistakes": [{"title": "<string>", "detail": "<string>",
"severity": "major|minor|info", "refs": [{"round": <int>, "t": <int>}]}],
"drills": [{"title": "<string>", "detail": "<string>",
"refs": [{"round": <int>, "t": <int>}]}]}
```

---

## Verification

Every rubric item in §1 and §2 names one of the 8 `kind` strings that
`coach_metrics.build_findings()` actually emits (grep hits listed at the top of this file and
confirmed again here):

```
grep -n '_finding(' bot/replay/coach_metrics.py | grep -oP '"\w+-\{slot\}"|"\w+_\w+", "\w+_\w+"'
-> first_death, first_blood, untraded_death, death_cluster, util_timing (x2), post_plant,
   survival_side, loss_involvement
```

Items marked OUT OF SCOPE (vdc.gg/tracker.gg/stream/OSINT/economy/agent-comp) have no
`coach_metrics` kind and are listed as such rather than mapped to one — per the task rule that
nothing may be mapped to a metric that does not exist.

§3's prompt text, retry text, and output schema are copied field-for-field from the shipped
`bot/replay/p3_coach.py`:

```
grep -n 'SYSTEM_PROMPT = (' -A 14 bot/replay/p3_coach.py    # prompt text, matches §3 verbatim
grep -n 'NARRATION_RETRY = (' -A 6 bot/replay/p3_coach.py   # retry text, matches §3 verbatim
grep -n 'MAX_MISTAKES\|MAX_DRILLS' bot/replay/p3_coach.py   # MAX_MISTAKES = 3, MAX_DRILLS = 6
```

`parse_narration`/`validate_narration` in that same file are the ground truth for the
`{summary, mistakes[{title,detail,severity,refs}], drills[{title,detail,refs}]}` shape — §3 does
not add, rename, or drop a field relative to what those functions read and return.
