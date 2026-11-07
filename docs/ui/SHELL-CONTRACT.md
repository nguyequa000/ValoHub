# v2 shell contract (foundation → all lanes)

This is the interface every page lane codes against. Foundation (lane F) implements it; page lanes must not fork it.

## Files

```
bot/web/static/replays_app/
  app.html                 # the only HTML document (no inline <script>)
  ui/tokens.css            # custom properties only
  ui/components.css        # primitives
  ui/app-shell.css         # chrome: titlebar, rail, status bar, space host
  ui/shell.js              # chrome + router + palette + toasts + api client (ES module)
  ui/boot.js               # imports shell + spaces, starts (ES module)
  ui/spaces/library.js     # one module per space, default export
  ui/spaces/replay.js
  ui/spaces/analysis.js
  ui/spaces/coach.js
  ui/spaces/providers.js
  ui/spaces/settings.js
  ui/spaces/library.css …  # one stylesheet per space (imported by its module or linked in app.html)
```

`app.html` is served for every app route; the shell routes on `location.pathname`.

## DOM (owned by F)

```html
<body>
  <div id="titlebar" class="titlebar">…drag region, #palette-trigger, #provider-chip, caption reserve…</div>
  <div class="app-body">
    <nav id="rail" class="rail" aria-label="Primary">…[data-testid=rail-library] etc…</nav>
    <main id="space" class="space" tabindex="-1"></main>
  </div>
  <footer id="statusbar" class="statusbar">…</footer>
  <div id="overlay-root"></div>
</body>
```

Required `data-testid` values (the gate asserts these):
`titlebar`, `palette-trigger`, `palette`, `provider-chip`, `rail`, `rail-library`, `rail-analysis`, `rail-coach`, `rail-providers`, `rail-settings`, `statusbar`, `status-parser`, `status-provider`, `space`, plus per-space ones declared in each lane's task.

All interactive elements carry an accessible name. Rail links are real `<a href>` so middle-click/back work, but the shell intercepts plain clicks and uses `history.pushState`.

## Routes

| path | space id | params |
|---|---|---|
| `/replays` | `library` | — |
| `/replay/{id}` | `replay` | `id`, `?t=` seconds, `?r=` round |
| `/analysis/{id}` | `analysis` | `id` |
| `/coach/{id}` | `coach` | `id` |
| `/providers` | `providers` | — |
| `/settings` | `settings` | `?section=` |

Unknown id → space renders its own "not found" state with a link to `/replays`. Shell never blanks: it shows a skeleton while the module loads, and an error card with Retry if `mount()` rejects.

## Space module contract

```js
// ui/spaces/library.js
export default {
  id: 'library',
  title: 'Library',
  accent: 'library',            // maps to CSS var --accent-space
  async mount(root, ctx) {},    // root = #space element; may be async
  unmount() {},                 // release listeners/timers/RAF
  onKey(e) {},                  // optional; called after global handlers, return true if handled
};
```

`ctx` is provided by the shell and is the only cross-lane API:

```js
ctx = {
  params,            // { id } (decoded)
  query,             // URLSearchParams
  navigate(path),    // pushState + route
  replace(path),
  api: {
    get(path),                       // → parsed JSON, throws ApiError {status, code, message}
    post(path, body),                // JSON
    del(path),
    stream(path, body, { onEvent, signal }), // SSE/fetch stream; onEvent({event,data})
  },
  ui: {
    toast(message, kind),            // 'info'|'success'|'error'
    confirm({title, body, confirmLabel, danger}),   // → boolean
    openModal(contentEl, {label}), closeModal(),
    setStatus({parser, provider, job}),             // partial update of the status bar
    skeleton(root, rows),            // loading placeholder
    empty(root, {icon, title, body, actionLabel, onAction}),
  },
  state: { get(key, fallback), set(key, value) },   // namespaced per space, localStorage, UI prefs only
  shortcuts: { register(map) },                     // { 'Ctrl+K': fn } scoped to the mounted space
};
```

Rules:
- A space renders only inside `root`; it must not touch `#titlebar`, `#rail`, `#statusbar` directly — use `ctx.ui.setStatus`.
- No global `window.onerror` handlers; let errors propagate to the shell.
- No `innerHTML` with API data. Build nodes with `document.createElement` or a small `el()` helper exported by the shell. Replay-derived strings are untrusted.
- Timers/RAF/listeners registered in `mount` are released in `unmount`.
- No inline event attributes; `addEventListener` only.

## API client

Same-origin only (`fetch` with `credentials:'same-origin'`), JSON, throws `ApiError` with `{status, code, message, requestId}`. Never logs credentials; never puts tokens in URLs. SSE via `stream()` reading `text/event-stream`, dispatching `{event,data}`; abort with `signal`.

## Visual primitives (from components.css)

`.btn` `.btn--primary` `.btn--ghost` `.btn--danger` `.icon-btn` `.chip` `.chip--active` `.field` `.select` `.toggle` `.segmented` `.tabs` `.card` `.stat` `.table` `.empty` `.toast` `.modal` `.drawer` `.progress` `.dot` `.kbd` `.provider-row` `.model-picker`.

Spacing/type tokens: `--sp-1…8` (4px steps), `--fs-caption|body|section|title`, `--h-control` (28px), `--h-row` (32px), `--radius-sm|md|lg`, `--accent` (interaction violet), `--accent-space` (per-space identity), `--surface-1|2|3`, `--text|--text-dim|--text-muted`, `--border`, `--focus-ring`.

## Motion and focus

120–160ms ease-out; `prefers-reduced-motion: reduce` disables transforms/auto-animation. Every control has `:focus-visible` ring 2px `--accent` + 2px offset. Focus is moved to `#space` after navigation and announced with `aria-live="polite"` on a shell-owned announcer.

## Persistence

`localStorage` keys are UI-only: `vp.rail.expanded`, `vp.density`, `vp.lastSpace`, `vp.reducedMotion`. **Never** provider keys, tokens, or model credentials. The gate checks this namespace for secret-shaped values.

## Addendum — frozen seams (from SPEC-REVIEW, 2026-11-03)

These were gaps; they are now contract, not lane choices.

### API client

```js
ctx.api = {
  get(path),                     // JSON
  post(path, body),              // JSON
  put(path, body),               // JSON
  del(path),
  upload(path, file, { onProgress, signal, fields }), // multipart PUT/POST via XHR; onProgress(0..1)
  stream(path, body, { onEvent, signal }),            // SSE
}
```

`upload` is required for library import (`PUT /api/replays/upload`, multipart field `file`, optional `name`). It must report progress and be abortable.

### Error envelope

Every non-2xx JSON response is `{"error": {"code": "...", "message": "...", "request_id": "...", "hint": "..."}}` and carries an `X-Request-Id` header. `ApiError` exposes `status`, `code`, `message`, `requestId`, `hint`. Never surface a raw exception string; 500s are generic + request id.

### SSE vocabulary

| endpoint | events |
|---|---|
| `/api/jobs/stream` | `progress` `{job_id, state, percent, stage, file}` · `done` `{job_id, replay_id}` · `error` `{code,message,request_id}` |
| `/api/ai/chat` | `token` `{text}` · `usage` `{input_tokens,output_tokens}` · `done` `{}` · `error` `{code,message,request_id}` |
| `/api/replays/{id}/coach` (stream) | `token`, `done`, `error` (same shape) |

A stream that ends without `done` is a failure: spaces must show an error, not a half report.

### Status bar types

`ctx.ui.setStatus({ parser: {text, level:'ok'|'warn'|'error'}, provider: {text, chip, level}, job: {state, text, percent}, build: {text} })`; `null` clears a segment. Strings are not accepted. Segments: `status-parser`, `status-provider`, `status-job`, `status-build`.

### Links

`ctx.links.moment(id, round, seconds)` → `/replay/{id}?r=<1-based int>&t=<float seconds>`. Invalid values are ignored by the replay space (clamped, never NaN). Deep links are the only share format — never include the loopback host/port.

### Optional ids

`/analysis` and `/coach` with no id are valid **index routes**: the space renders a replay picker, never a fetch to `undefined`. `/replay` without id redirects to `/replays`.

### Post-mount errors

The shell listens for `unhandledrejection` and shows a toast with `code` + `request_id`; a 404/410 from a space action routes to a not-found state without a Retry loop. Spaces keep filters/state when an action fails.

