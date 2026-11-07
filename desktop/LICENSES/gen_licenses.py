"""Generate desktop LICENSES folder content. Usage:
  py -3.12 desktop/LICENSES/gen_licenses.py --repo <repo> --out <dir> [--dotnet-src <dir>] [--venv-python <python.exe>]
Copies vendor LICENSE, THIRD-PARTY-NOTICES.md, third-party/*, dotnet ThirdPartyNotices.txt (when present),
and writes PIP-LICENSES.txt (from installed dist metadata of the build venv) + a README from README.template.txt.
Missing optional sources are reported, never fatal."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dotnet-src", default="")
    ap.add_argument("--vendor-src", default="")
    ap.add_argument("--venv-python", default=sys.executable)
    a = ap.parse_args()
    repo, out = Path(a.repo), Path(a.out)
    vendor = Path(a.vendor_src) if a.vendor_src else repo / "vendorValorantReplayParser"
    out.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []

    def cp(src: Path, dst_name: str) -> None:
        if src.is_file():
            shutil.copy2(src, out / dst_name)
            copied.append(dst_name)
        else:
            missing.append(str(src))

    cp(vendor / "LICENSE", "vendorValorantReplayParser-LICENSE.txt")
    cp(vendor / "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md")
    # The 2D viewer is a port of talhakoek/ValorantWebReplayer (MIT); its notice must ship
    # with the binaries and is tracked in the viewer tree, not in the ignored reference clone.
    cp(repo / "bot/web/static/replays_app/LICENSE-ValorantWebReplayer",
       "viewer-ValorantWebReplayer-LICENSE.txt")

    tp = vendor / "third-party"
    if tp.is_dir():
        for f in sorted(tp.rglob("*")):
            if f.is_file():
                name = "third-party-" + "-".join(f.relative_to(tp).parts)
                shutil.copy2(f, out / name)
                copied.append(name)
    else:
        missing.append(str(tp))
    if a.dotnet_src:
        cp(Path(a.dotnet_src) / "ThirdPartyNotices.txt", "dotnet-ThirdPartyNotices.txt")
        cp(Path(a.dotnet_src) / "LICENSE.txt", "dotnet-LICENSE.txt")

    code = (
        "import importlib.metadata as m,json;"
        "print(json.dumps([[d.metadata['Name'],d.version,d.metadata.get('License-Expression') or d.metadata.get('License') or '',"
        "[c for c in (d.metadata.get_all('Classifier') or []) if c.startswith('License')]] for d in m.distributions()]))"
    )
    r = subprocess.run([a.venv_python, "-c", code], capture_output=True, text=True)
    lines = ["Python packages bundled in backend/site-packages (embedded CPython 3.12 sidecar)", ""]
    if r.returncode == 0:
        for n, v, lic, cl in sorted(json.loads(r.stdout), key=lambda x: x[0].lower()):
            lines.append(f"{n} {v} | {(lic.splitlines() or [''])[0][:80]} | {'; '.join(cl)}")
    else:
        lines.append("(pip license metadata unavailable: " + r.stderr.strip()[:200] + ")")
    (out / "PIP-LICENSES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tpl = Path(__file__).with_name("README.template.txt")
    (out / "README.txt").write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps({"copied": copied, "missing": missing}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
