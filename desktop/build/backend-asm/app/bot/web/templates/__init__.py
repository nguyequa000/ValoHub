"""Minimal server-side HTML rendering.

No template engine is installed (neither aiohttp_jinja2 nor Jinja2), so pages
are assembled from plain strings and injected into one shared layout. Every
page goes through layout() so the nav bar and styling stay consistent.
"""
import html


def esc(value) -> str:
    """HTML-escape a value for safe interpolation."""
    return html.escape(str(value))


_LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - Scouting</title>
<style>
  :root {
    --bg: #05080b;
    --bg-alt: #0b0f14;
    --panel: #131a22;
    --border: #232f3b;
    --text: #eef2f6;
    --muted: #8a93a0;
    --accent: #ff4655;
    --accent-dark: #d8323f;
    --ok: #57d98a;
    --warn: #ffb454;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: "Segoe UI", system-ui, Roboto, sans-serif; }
  header { display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
           background: var(--bg-alt); padding: 14px 24px;
           border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 10; }
  header .brand { font-weight: 800; letter-spacing: 0.5px; color: var(--text);
                  text-decoration: none; font-size: 1.1rem; display: flex; align-items: center; gap: 8px; }
  header .brand .dot { width: 10px; height: 10px; border-radius: 2px; background: var(--accent);
                        transform: rotate(45deg); display: inline-block; }
  header a.navlink { color: var(--muted); text-decoration: none; font-weight: 600; font-size: 0.92rem; }
  header a.navlink:hover { color: var(--text); }
  header form { margin-left: auto; display: flex; gap: 8px; }
  main { padding: 32px 24px; max-width: 1100px; margin: 0 auto; }
  h1 { margin-top: 0; font-weight: 800; letter-spacing: -0.3px; }
  h2 { font-weight: 700; }
  a { color: #7fd1ff; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
  th { background: var(--panel); color: var(--muted); text-transform: uppercase;
       font-size: 0.78rem; letter-spacing: 0.4px; }
  tr:hover td { background: rgba(255,255,255,0.02); }
  pre { background: var(--panel); border: 1px solid var(--border); padding: 14px;
        border-radius: 8px; overflow: auto; }
  .hero { background: linear-gradient(160deg, var(--panel), var(--bg-alt));
          border: 1px solid var(--border); border-radius: 14px; padding: 32px;
          margin: 0 0 24px 0; }
  .hero h1 { font-size: 1.9rem; }
  .scout-form { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
          padding: 16px 18px; margin: 12px 0; transition: border-color 0.15s ease; }
  .card-link { display: block; text-decoration: none; color: inherit; }
  .card-link:hover { border-color: var(--accent); }
  .card h2 { margin: 0 0 6px 0; font-size: 1.05rem; color: var(--text); }
  .grid { display: flex; gap: 14px; flex-wrap: wrap; }
  .grid .card { flex: 1 1 240px; margin: 0; }
  .muted { color: var(--muted); }
  .ok { color: var(--ok); }
  .warn { color: var(--warn); }
  img { max-width: 100%; border: 1px solid var(--border); border-radius: 6px;
        margin: 8px 0; background: #fff; }
  input, select { background: var(--bg-alt); border: 1px solid var(--border); color: var(--text);
                  padding: 9px 12px; border-radius: 6px; font-size: 0.95rem; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .btn { display: inline-block; border: 0; color: #fff; padding: 9px 18px;
         border-radius: 6px; cursor: pointer; font-weight: 700; font-size: 0.92rem;
         letter-spacing: 0.2px; text-decoration: none; transition: background 0.15s ease, transform 0.05s ease; }
  .btn:active { transform: translateY(1px); }
  .btn-primary { background: var(--accent); }
  .btn-primary:hover { background: var(--accent-dark); }
  .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .btn-outline:hover { border-color: var(--accent); color: var(--accent); }
  button.btn { font-family: inherit; }
</style>
</head>
<body>
<header>
  <a class="brand" href="/"><span class="dot"></span>Scouting</a>
  <a class="navlink" href="/replays">Replays</a>
  <form onsubmit="return goPlayer(this)">
    <input name="q" placeholder="Riot ID (Name#Tag)" size="20" autocomplete="off">
    <button class="btn btn-primary" type="submit">Scout</button>
  </form>
</header>
<main>__BODY__</main>
<script>
function goPlayer(form) {
  var value = form.q.value.trim().replace(/#/g, "-");
  if (value) { window.location.href = "/players/" + encodeURIComponent(value); }
  return false;
}
</script>
</body>
</html>
"""


def layout(title, body: str) -> str:
    """Wrap page body HTML in the shared nav/style layout."""
    return _LAYOUT.replace("__TITLE__", esc(title)).replace("__BODY__", body)


def error_page(message: str) -> str:
    return layout(
        "Error",
        f"<h1>Error</h1><div class='card'>{esc(message)}</div>"
        "<p><a href='/'>&larr; Back to dashboard</a></p>",
    )


def table(headers, rows) -> str:
    """Render a table. Cells are treated as pre-escaped HTML."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
