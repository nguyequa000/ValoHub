# ValoHub UI v2 — DESIGN (binding)

Binding design contract for the current UI shell.

## 1. Principles

1. **One window, persistent chrome.** Navigation never reloads the document.
2. **Every region earns its space.** No inert corner, no control without a handler, no fake status. Empty = purposeful empty state with one action.
3. **Color carries meaning.** One interaction accent; per-space identity accents; semantic red/amber/green reserved for state.
4. **Keyboard first.** Palette, shortcuts, visible focus, no traps.
5. **Offline by default.** Cloud AI is explicit, per-provider, consented opt-in.
6. **Evidence-first.** Every number comes from the API or a labeled computation.

## 2. Window and chrome

- `BrowserWindow({ width:1440, height:900, minWidth:900, minHeight:560, titleBarStyle:'hidden', titleBarOverlay:{ color:'#0B0F16', symbolColor:'#E7ECF5', height:40 }, backgroundColor:'#0B0F16' })`; optional `setBackgroundMaterial('mica')` behind a Win11 check, silent fallback.
- **No visible menu.** `Menu.setApplicationMenu(minimalMenu)` with `win.setMenuBarVisibility(false)` and `autoHideMenuBar: true`; `Alt` reveals it. No `File/Edit/View` strip on screen.
- **Titlebar (40px, drag):** product mark + `ValoHub`; current space; centered palette trigger `Search or jump to… Ctrl K`; provider chip (`Local · llama.cpp` / `OpenAI · gpt-…` / `No model`); native caption reserve.
- **Rail (56px, 220px on expand):** Library, Analysis, Coach, AI Providers, Settings; footer: backend status dot + version. Active = accent pill + icon + label, `aria-current="page"`.
- **Context bar (48px):** space title + factual subtitle, primary actions right. No breadcrumbs, no `↗` links.
- **Status bar (24px):** parser state, provider/model, active job progress, shortcut hint, build id. Every segment is a button.
- Grid: `rail | titlebar / context / content / status`; `body{overflow:hidden}`; only panes scroll.
- Compact ≤1100px: rail labels hide, inspector collapses, secondary actions move to overflow.

## 3. Visual language

- Base `#0B0F16`; surfaces `#111725`, `#16202F`, `#1C2839`; border `#26344A`; text `#E7ECF5`, `#A9B4C6`, `#6F7C91`.
- **Brand (0.5.0):** ValoHub uses the "Converge" mark — three feed bars (9u/6u/9u) flush against a 9×15 hub slab on a 24-unit grid, axis-aligned so it stays crisp at 16 px. `bot/web/static/replays_app/brand/mark.svg` is the monochrome `currentColor` source; `ui/brand.css` applies it as a 20px titlebar mask and a 40px About mask. The program icon plates it on `#111725` with the hub in lifted violet `#8B72F5` (`desktop/electron/build/make_icons.py`).
- **Interaction accent (focus, selection, primary):** violet `#7C5CFF`.
- **Space identity accents** (headers, tints, chart series — never body text): Library `#22C7B8` teal, Replay `#F2555A` red, Analysis `#9B7BFF` violet, Coach `#F5A524` amber, Providers `#38BDF8` sky, Settings `#94A3B8` slate.
- Tinted section headers: accent at 12% alpha + 3px left/top edge; accent-colored stat chips and chart series. Gradients only in empty-state art, never behind text.
- Type: Segoe UI Variable → Segoe UI. 12/16 caption, 13/18 body, 16/22 section, 20/28 title; data in Cascadia Mono 12/16 `tabular-nums`.
- Contrast: 4.5:1 text, 3:1 UI/icons; focus ring 2px accent + 2px offset, never removed. Targets ≥24×24.
- Motion 120–160ms; `prefers-reduced-motion` disables transforms.
- Density: 4px grid; control heights 24/28/32; rows 28 (compact) / 32 (comfortable).

## 4. Spaces

### 4.1 Library `/replays`
- Toolbar: search, filter chips (map, result, agent, patch), sort, grid/list toggle, `Import` primary.
- Grid cards: map thumb, score, result badge, duration, patch, agents; selected card gets accent edge.
- Right pane (always functional): selected replay summary + top duelists + last coach verdict + `Open replay` / `Analyze`; when nothing selected it is the import drop zone with recent imports.
- Import: drop anywhere in content; stages upload→parse→normalize→ready; cancel; per-file error.
- Empty: hero empty state that is the drop target, `Import .vrf` and `Open demo`.
- No parser/decoder UI here (it lives in Settings ▸ Parser).

### 4.2 Replay workspace `/replay/{id}`
- Header (44px): back chevron to Library, map/score/patch, workspace switch `Replay · Analysis · Coach`.
- Map fills; HUD round/time; legend; warnings.
- Transport dock 92px: prev/play/next, clock/duration, 28px scrub + event ticks (24px hit), speed, layer toggles, player/team filters, key legend.
- Inspector 320–360px, tabs Rounds / Events / Players / Coach, internal scroll, `Ctrl+I` collapse.
- Context actions: `Share moment` (copies deep link), `Watch together`, `Bookmark round`.
- Keys: Space, `←/→` 1s, `Shift+←/→` 5s, `J/K/L`, `[`/`]`, `1–4` tabs, `F11` map, `Esc` back.

### 4.3 Analysis `/analysis/{id}`
- Left filters 240px (layer, round range, player, team) + saved views.
- Center heatmap + legend (cell size, peak cell, band counts) + layer switch.
- Right findings with severity and `Jump to moment` (deep links into Replay).
- Post-0.4.1 decision: retain the bounded findings pane (nested scrolling); at 1440×900 the reviewed list has 10,324px scrollHeight / 632px clientHeight while `#space` stays at 1,793px, below the 1,800px budget. The trade-off is a second scroll region instead of a single long page; retaining it keeps the map and summary reachable without adding the full findings height to the page.
- Below: round outcome table, economy chart, duel matrix — rows/series interactive.
- Export: copy findings (Markdown), save heatmap PNG.

### 4.4 Coach `/coach/{id}`
- Three columns: sessions | report | context (player, provider+model, token estimate, Regenerate).
- Streaming report with stop; findings link to moments; rules-only fallback clearly labeled.
- No provider: inline card with local options + CTA to Providers; rules-only still works.

### 4.5 AI Providers `/providers`
- Two groups, never one dropdown: **On this PC** and **Cloud providers**.
- Local rows auto-detected: managed llama.cpp, LM Studio `127.0.0.1:1234`, Ollama `127.0.0.1:11434` (host-branched: `ollama.com` is cloud, not local).
- Cloud rows: OpenRouter (OAuth or key), OpenAI, Anthropic, Google Gemini, Custom OpenAI-compatible (loopback/RFC1918 only).
- Row state: `Connected · N models` / `Not connected` / `Needs key` / `Invalid key (401)` / `Forbidden (403)` / `Rate limited (429)` / `Unreachable` — with the reason text.
- Connect sheet: verify-then-save (`GET {base}/models`, then a 1-token chat), OAuth button where supported, `Get key` opens the provider console in the system browser, masked key + Replace/Remove.
- Model picker: searchable, grouped by provider, LOCAL/CLOUD badge, context/capability where reported, cached list + Retry on failure.
- Actions: `Use as default`, `Test`, `Disconnect` (revoke when supported, then delete).
- Cloud consent sheet names the host and the data sent (`replay summary + coach instructions`); nothing is sent before `Enable <Provider>`.

### 4.6 Settings `/settings`
- Sections: AI Providers (link), Appearance (theme, density, reduced motion), Parser (decoder status/update/dry-run), Data & Privacy (data folder, open in Explorer, clear cache, egress log), About (version, build commit, licenses).
- Esc closes Settings; `Ctrl+,` opens it from anywhere.

## 5. Components

`ui/tokens.css` + `ui/components.css` + `ui/app-shell.css` + `ui/shell.js` are the only sources for: Button, IconButton, Chip, Field, Select, Toggle, Segmented, Tabs, Card, Stat, DataTable, EmptyState, Toast, Modal, Drawer, Progress, StatusDot, Kbd, CommandPalette, ProviderRow, ModelPicker, ConsentSheet.
Pages compose these classes; no page-specific button/field/card CSS.

## 6. AI provider backend

- Registry (compiled): `id, label, kind(local|cloud), base_url, auth(oauth_pkce|api_key|none), models_path, chat_path, extra_headers, console_url, oauth{authorize_url, token_path, revoke_path}`.
- Endpoints: `GET /api/ai/providers`, `POST /api/ai/providers/{id}/key`, `POST /api/ai/providers/{id}/oauth/start`, `GET /api/ai/oauth/callback`, `DELETE /api/ai/providers/{id}`, `GET /api/ai/providers/{id}/models`, `POST /api/ai/providers/{id}/test`, `POST /api/ai/chat`.
- Secrets: DPAPI (`CryptProtectData`, `CRYPTPROTECT_UI_FORBIDDEN`, entropy = build constant + provider id) into `<data_root>/secrets/providers.bin`, atomic replace, ACL to the user; decrypt failure → delete + `reauth_required`. Response objects contain `configured`, `last4`, never the key.
- OAuth (OpenRouter only): PKCE S256, 256-bit state, loopback `127.0.0.1:{ephemeral}/callback`, system browser via `os.startfile`, 5-minute timeout, single response, token exchanged server-side, stored sealed. Disconnect: best-effort revoke, then delete.
- Cloud calls: catalog host only, `allow_redirects=False`, `trust_env=False`, TLS verify, timeout, `store:false` where the provider offers it (xAI deferred). Errors returned as `{code,status,request_id}`.
- Coach sends a pseudonymized summary (player names replaced, Riot IDs dropped), never the `.vrf` or raw NDJSON.
- Offline default: `VALO_OFFLINE=1`; local probe chain unchanged; cloud requires consent record `{provider, consented_at, policy_version, hosts}`.

## 7. Security invariants

1. Renderer: `sandbox`, `contextIsolation`, `nodeIntegration:false`, `webviewTag:false`.
2. CSP from the backend: `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`. No inline `<script>`; boot code in `ui/boot.js`.
3. Renderer never receives a key/token; nothing in localStorage/IndexedDB/cookies/DOM; no key in URLs.
4. All provider traffic from the Python sidecar; base URLs from the catalog; no redirects; no proxy env.
5. No secret or upstream body in logs/errors; redaction at the sink.
6. Provider list responses are cached only as `{id,label,models:[...]}` with no credentials.
7. Disconnect deletes the sealed blob entry and (where supported) revokes; `invalid_grant` purges and prompts.

## 8. Lane ownership (one writer per file)

The file tree and markup ownership are defined **only** in `SHELL-CONTRACT.md`. Summary: one `app.html` shell, six `ui/spaces/*.js` modules, one stylesheet per space. There are no per-page HTML files; `index.html` / `view.html` / `analysis.html` are v1 and are deleted at handoff.

| Lane | Owns |
|---|---|
| F foundation | `app.html`, `ui/tokens.css`, `ui/components.css`, `ui/app-shell.css`, `ui/shell.js`, `ui/boot.js`, space stubs |
| L library | `ui/spaces/library.js`, `ui/spaces/library.css` |
| R replay | `ui/spaces/replay.js`, `ui/spaces/replay.css` |
| A analysis | `ui/spaces/analysis.js`, `ui/spaces/analysis.css` |
| C coach | `ui/spaces/coach.js`, `ui/spaces/coach.css` |
| S providers/settings | `ui/spaces/providers.js`, `ui/spaces/settings.js`, `ui/spaces/settings.css`, `ui/spaces/providers.css` |
| P backend | `bot/replay/ai_*.py`, `bot/web/ai_routes.py`, `bot/web/desktop.py`, `bot/replay/api.py`, CSP, redaction, tests |
| E electron | `desktop/electron/src/main.js`, `window.js`, `policy.js` |
| T gate | `tools/desktop_qa/ui_gate*.mjs`, fixtures |

Cross-lane seam rules: `ctx.api` and `ctx.ui` are the only cross-space API; `ctx.links.moment(id, r, t)` builds moment links; the shell owns `#titlebar`, `#rail`, `#context-bar`, `#statusbar`.

## 9. Acceptance

- Packaged app, isolated home: all six surfaces reachable, no dead control, no visible menu, drag + native caption works, window state persists.
- Provider flow with a stub provider: verify-then-save, invalid key, rate-limited, model list, default, disconnect; no secret in renderer storage (gate check).
- Gate green at 900×600, 1280×800, 1440×900, 1920×1080, 768×1024 (compact), keyboard-only journey, contrast AA, no overflow.
- `pytest bot` + new backend tests green; `pytest` whole-repo collects cleanly.
- Artifact: version 0.3.0, SHA-256 + BUILD-INFO recorded in `INTEGRATION.md`.
