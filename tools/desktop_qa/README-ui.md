# UI acceptance gate — v2 single-page shell

`tools/desktop_qa/ui_gate.mjs` is the acceptance gate for the **v2** desktop UI. It drives a real
Chromium page over raw CDP (no npm dependency, no Playwright/Puppeteer) and asserts the contract in
`docs/ui/DESIGN.md` §9 and `docs/ui/SHELL-CONTRACT.md`.

**The v1 checks are gone, not kept.** v1 navigated `index.html` / `view.html` / `analysis.html` as
separate documents and scanned their DOM (`a1-horizontal-overflow`, `b-card-above-fold`,
`c-transport-in-viewport`, `d-tick-click-seeks`, `e-single-h1`, `f1/f2`, `g1/g2`, `h-contrast`). v2 is one
`app.html` shell that routes on `location.pathname`, so every v1 check was replaced. The v1 recorded
baseline under `baseline-ui/` is historical evidence for `desktop/ui-base` and is no longer comparable.

| File | Role |
| --- | --- |
| `ui_gate.mjs` | CLI + server/app launcher + CDP driver + check evaluation + report writer |
| `ui_gate_probe.mjs` | Injected page-side probe (pure function) + WCAG maths (`contrastRatio`, `contrastThreshold`, `hitAreaClass`) |
| `ui_gate_selftest.mjs` | Proves the gate is not vacuous: clean page green + mutation test |
| `fixtures/home` | Isolated home fixture (`data/replays/v2gate`) used for every lane run |

## Run

```sh
# Dev server on an isolated home (the normal lane run)
node tools/desktop_qa/ui_gate.mjs \
  --home VALOPLANT_WORK/tmp/ui-v2-evidence/gate-home \
  --fixture tools/desktop_qa/fixtures/home \
  --port 19643 --browser-port 19653 \
  --out VALOPLANT_WORK/tmp/ui-v2-evidence/gate

# Against an already-running server
node tools/desktop_qa/ui_gate.mjs --base-url http://127.0.0.1:19631 --token "$TOK" --out out/gate

# Packaged app (native menu / caption buttons are MANUAL — see below)
node tools/desktop_qa/ui_gate.mjs --home C:/tmp/v2-home --exe "C:/.../ValoHub.exe" --out out/gate-exe

node tools/desktop_qa/ui_gate.mjs --list-checks     # stable check ids
node tools/desktop_qa/ui_gate_selftest.mjs          # self-test (mutation test)
```

Exit codes: `0` pass, `1` contract failure(s), `2` harness error (bad args, server never ready, no browser).
`--fail-on-warn` promotes warnings to failures, except the documented unverifiable classes (currently
only `v4-contrast` "text over an image/gradient cannot be verified mechanically"); those stay warnings so
a release run can still exit 0.

### CLI compatibility

`--home`, `--exe`, `--replay-id`, `--port`, `--browser-port`, `--out`, `--fail-on-warn` keep their v1 names
and meaning. Added in v2: `--base-url`, `--token`, `--routes`, `--skip`, `--settle-ms`, `--ready-ms`,
`--browser`, `--list-checks`.

### Safety rails

* Ports must be in the lane range `19600-19699`; **18792 (the user's live app) is refused**.
* `%LOCALAPPDATA%\ValoHub` is refused as `--home`; the gate only grades an isolated copy.
* `--fixture` is copied into `--home` and never mutated; round files are copied from the product demo
  store rather than duplicated into the repo.
* In `--exe` mode the gate reads `/api/app/info` from inside the page and hard-fails unless
  `paths.data_root` equals the resolved `--home`, so a run can never grade live data.

## Check table

Every check runs per route (and per viewport for the visual set). `fail` exits non-zero; `warn` is
reported only, unless `--fail-on-warn`.

| id | assertion |
| --- | --- |
| `r1-shell` | titlebar + rail + statusbar + non-empty space; a space still on its loading skeleton counts as empty |
| `r2-console` | no console error / uncaught exception |
| `r3-network-scope` | every request stays on loopback (127.0.0.1 / localhost / [::1]) |
| `c1-no-dom-menu` | no in-DOM menu bar (the native Electron menu is a MANUAL step) |
| `c2-drag-region` | a `-webkit-app-region: drag` region spans the 40 px titlebar |
| `c3-rail-5` | 5 rail items, distinct hrefs, exactly one `aria-current="page"`, on the item that owns the mounted space |
| `c4-single-brand` | the product name appears once in chrome |
| `c5-no-arrow-links` | no `↗` in any interactive name |
| `c6-no-breadcrumbs` | no breadcrumb nav / class / testid |
| `d1-accessible-name` | every visible control has a non-empty accessible name |
| `d2-affordance` | every enabled control has an href or a handler; every disabled control has a reason |
| `d3-click-probe` | a scripted click on every enabled, non-destructive control changes something |
| `k1-palette-open` | Ctrl+K opens the palette and focuses its input |
| `k2-palette-filter` | typing narrows the palette list |
| `k3-palette-enter` | Enter runs the command and navigates |
| `k4-palette-esc` | Esc closes the palette (focus restore is a warn) |
| `k5-tab-order` | Tab from the top of the document reaches rail → context → content |
| `k6-focus-ring` | keyboard focus paints a computed outline/box-shadow |
| `p1-provider-flow` | connect → models → default → test → disconnect against a stub provider |
| `p2-invalid-key` | a rejected key renders `Invalid key (401)` |
| `p3-no-secrets` | no key in localStorage/sessionStorage/IndexedDB/DOM/URL (exact + `sk-`, `or-`, 40-char shapes) |
| `v1-overflow` | no horizontal overflow at 900×600, 1280×800, 1440×900, 1920×1080, 768×1024 |
| `v2-primary-visible` | the space's primary action is not clipped by the viewport |
| `v3-target-size` | every visible target is ≥24×24 CSS px |
| `v4-contrast` | sampled text ≥4.5:1 (3:1 for large text); text over an image/gradient is `warn`, never a pass |
| `v5-reduced-motion` | `prefers-reduced-motion: reduce` zeroes transitions/animations |
| `m1-mount-budget` | a space mount measures < 250 ms (`performance.now` around navigation) |
| `m2-layout-shift` | cumulative layout shift after load stays at 0 |
| `s1-csp-header` | every app response carries the exact v2 CSP |
| `s2-inline-blocked` | an injected inline script does not execute (and a `securitypolicyviolation` fires) |

### Rules the gate is opinionated about (learned on the real app)

* **Mount wait.** Wait until `#space` holds a **non-skeleton** child, not merely any child. Spaces that
  fetch (providers, coach) mount `[data-testid="skeleton"]` first; probing at "first child" reported a
  correctly rendered space as empty.
* **Rail ownership.** `/replay/:id` mounts the replay space but belongs to the **Library** rail item
  (`RAIL_OF_SPACE`), so the correct `aria-current` there is `library`.
* **Tab order starts at the top.** The focus probe starts from the first titlebar control; starting inside
  `#space` can only ever measure what comes after the content.
* **Dead controls.** A control is dead when a real hit-tested click changes nothing. Three documented
  escapes: `DESTRUCTIVE` labels; controls already in their on-state (`aria-pressed/selected/checked/current`
  — re-clicking the active segment is a no-op by design); and `STATE_DEPENDENT` labels (reset/refresh/close,
  `provider status` and `ai provider: ` which both navigate to a route that may already be mounted,
  `new review`, `previous/next round` at a boundary). A selector that disappears because an earlier probe
  re-rendered the space is reported `stale`, not dead.
* **Change signature** includes `#space.scrollTop` and `window.scrollY`: a "jump to section" control changes
  only scroll position.

## Output

`report.json` (`valoplant_ui_gate/v2`: target, per-route `shell`/`results`/`clickProbe`, `findings[]`,
`summary.byCheck`, `summary.failedChecks`, `manual[]`), `summary.txt`, one PNG per route per viewport,
`server.log` when the gate started the server.

## MANUAL steps (not assertable over CDP in a browser tab)

1. **Native menu bar hidden.** Launch the packaged app, look at the window, press `Alt` once: no
   File/Edit/View strip may appear, and nothing may stay revealed after 1 s. `c1-no-dom-menu` only proves
   there is no *HTML* menu.
2. **Drag + native caption buttons.** Drag the window by the titlebar; the minimise/maximise/close set must
   be the native ones and window state must persist across restarts.

Both are emitted into `report.json → manual[]` on every run so they cannot be silently skipped.

## Self-test — `ui_gate_selftest.mjs`

Proves the gate catches a broken DOM, not just that it passes:

1. WCAG maths (contrast ratio, threshold, hit-area classes, colour parsing).
2. A clean synthetic v2 shell produces **zero** fail findings.
3. **Mutation test** — 15 deliberate DOM/CSS breakages, each must flip *exactly* the expected check id:
   removed statusbar, emptied space, duplicated brand, menubar, un-draggable titlebar, sixth rail item,
   `↗` link, breadcrumbs, nameless + unexplained buttons, page overflow, 8 px target, low-contrast text,
   motion under `reduce`, **stuck skeleton**, **two lit rail items**. Collateral failures fail the mutation,
   which is what stops a "fail everything" gate from passing.
4. CSP differential — the same inline script executes on a page served without a policy and is blocked
   (with a `securitypolicyviolation`) on a page served with the v2 policy.

The clean fixture loads its script as an **external** `/clean.js` because it is served with
`script-src 'self'`: an inline `<script>` would be silently blocked and the "wired control" probe would
then be indistinguishable from a dead control.
