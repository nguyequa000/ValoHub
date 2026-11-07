"""Non-fatal first-run diagnostics for run_bot.bat; never inspect replay sources.

All checks stay inside this checkout. A socket probe only checks whether a local
LLM port accepts connections; it does not send prompts, tokens or read a descriptor.
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "bot" / "web" / "static" / "replays_app"
DLL = ROOT / "vendorValorantReplayParser" / "src" / "CliReader" / "bin" / "Release" / "net10.0" / "CliReader.dll"


def report(label: str, value: str) -> None:
    print(f"[first-run] {label}: {value}", flush=True)


def writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as stream:
            stream.write(b"ok")
        return True
    except OSError:
        return False


def llm_endpoint() -> str | None:
    """Report one accessible configured local socket, not model availability."""
    urls = (
        os.getenv("VALO_LLM_URL"),
        os.getenv("VALO_LMSTUDIO_URL") or "http://127.0.0.1:1234/v1",
        os.getenv("VALO_OLLAMA_URL") or "http://127.0.0.1:11434",
    )
    for url in urls:
        if not url:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                continue  # Never contact a remote endpoint during first-run checks.
            with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=0.3):
                return f"local socket reachable ({parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}); model is checked on request"
        except (OSError, ValueError):
            continue
    return None


def main() -> None:
    try:
        report("CliReader.dll", "present" if DLL.is_file() else
               "missing; replay import needs .NET 10 SDK + parser build (see bot/replay/tools/update_parser.py)")
        assets = STATIC / "assets"
        report("asset pack", "present" if (assets / "agents" / "index.json").is_file() and
               any((assets / "maps").glob("*.png")) else
               "missing/incomplete; viewer will use remote artwork or fallback backgrounds")
        data = ROOT / "data" / "replays"
        report("data/replays", "writable" if writable(data) else
               "not writable; import jobs may fail (check folder permissions)")
        endpoint = llm_endpoint()
        report("LLM", endpoint or "no probed local socket reachable (VALO_LLM_URL, LM Studio, Ollama); managed llama.cpp descriptor not checked; coach checks on request")
    except Exception as exc:  # Diagnostics must never stop the web server.
        report("warning", f"diagnostic could not complete ({type(exc).__name__}); continuing")


if __name__ == "__main__":
    main()
