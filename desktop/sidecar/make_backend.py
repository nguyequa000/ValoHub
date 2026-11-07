"""Assemble the ValoHub Python backend folder (replaces the PyInstaller freeze).

Layout written to --out (staged as resources/backend):
    python.exe, python312.dll, ...   official python.org embeddable CPython 3.12 (PSF-signed, unmodified)
    python312._pth                   isolated sys.path: python312.zip, ., site-packages, app
    site-packages/                   aiohttp + deps (pip --target, binary wheels only)
    app/bot/...                      the bot package (no tests/caches) + static viewer + replay json

Launch: backend/python.exe -m bot.sidecar serve --port P --data-dir D
Stdlib only; pip is run through --venv-python.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
EMBED_MD5 = "fe8ef205f2e9c3ba44d0cf9954e1abd3"  # python.org download page value for this file

PTH = "python312.zip\n.\nsite-packages\napp\n"


def _md5(p: Path) -> str:
    h = hashlib.md5()
    h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--venv-python", required=True, help="python 3.12 win64 used only to run pip --target")
    ap.add_argument("--requirements", required=True)
    ap.add_argument("--embed-zip", default="", help="local embeddable zip (default: download EMBED_URL)")
    ap.add_argument("--assets-src", default="", help="external replays_app/assets when the checkout lacks it")
    a = ap.parse_args()
    repo, out = Path(a.repo).resolve(), Path(a.out).resolve()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    ez = Path(a.embed_zip) if a.embed_zip else out.parent / "python-3.12.10-embed-amd64.zip"
    if not ez.is_file():
        print(f"download {EMBED_URL}")
        urllib.request.urlretrieve(EMBED_URL, ez)
    if _md5(ez) != EMBED_MD5:
        print(f"embeddable zip md5 mismatch: {_md5(ez)} != {EMBED_MD5}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(ez) as z:
        z.extractall(out)
    (out / "python312._pth").write_text(PTH, encoding="ascii")

    site = out / "site-packages"
    subprocess.run([a.venv_python, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
                    "--only-binary", ":all:", "--target", str(site), "-r", a.requirements], check=True)
    for junk in site.glob("bin"):
        shutil.rmtree(junk, ignore_errors=True)

    app = out / "app"
    src = repo / "bot"
    for f in sorted(src.rglob("*.py")):
        rel = f.relative_to(src)
        if "__pycache__" in rel.parts or rel.name.startswith("test_") or rel.name == "conftest.py":
            continue
        (app / "bot" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, app / "bot" / rel)
    static = src / "web/static/replays_app"
    shutil.copytree(static, app / "bot/web/static/replays_app", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    if not (static / "assets").is_dir() and a.assets_src:
        shutil.copytree(a.assets_src, app / "bot/web/static/replays_app/assets", dirs_exist_ok=True)
    for j in sorted((src / "replay").glob("*.json")):
        shutil.copy2(j, app / "bot/replay" / j.name)

    # hash-based pycs (never revalidated, valid whatever the extraction mtime); the shell sets
    # PYTHONDONTWRITEBYTECODE=1 so the installed tree is never written to at run time.
    subprocess.run([a.venv_python, "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash",
                    str(site), str(app)], check=True)

    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"backend assembled: {out} ({n} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
