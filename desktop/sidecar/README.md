# desktop/sidecar - Python backend folder (`resources/backend/python.exe -m bot.sidecar`)

Since the F1 repair the backend is **not frozen**. A PyInstaller `vrb.exe` was classified by Microsoft Defender as
`Trojan:Win32/Bearfoos.A!ml` (ML heuristic) and quarantined on the build machine, so the sidecar now runs on the
official, PSF-signed **embeddable CPython 3.12.10** (python.org `python-3.12.10-embed-amd64.zip`, md5 checked) with the
bot package and `site-packages` beside it. No custom executable is produced.

`make_backend.py` (run by `desktop/build.ps1` step c) writes:

    backend/python.exe, python312.dll, ...   unmodified embeddable runtime
    backend/python312._pth                   python312.zip / . / site-packages / app   (isolated sys.path, no `site`)
    backend/site-packages/                   aiohttp + deps (`pip --target`, binary wheels only; requirements-sidecar.txt)
    backend/app/bot/...                      bot package without tests, static viewer, replay json; hash-based .pyc

Launch (what `desktop/electron/src/main.js` does): `backend/python.exe -m bot.sidecar serve --port P --data-dir D`
with env `VRB_TOKEN`, `VALOHUB_BUNDLE=<resources>`, `VALO_OFFLINE=1`, `PYTHONDONTWRITEBYTECODE=1`.
`bot.sidecar` also has `--version`, `--self-test` and `normalize <raw> <viewer>`; the pipeline runs the normalizer as
`python.exe <app>/bot/replay/normalize.py` (the unfrozen path of `pipeline.normalizer_command`).

Build everything with one command (from repo root):

    powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1 [-Version 0.1.0] [-SkipElectron] [-NoZip]
        [-WheelHouse dir] [-DotnetSrc dir] [-DecoderSrc dir] [-Record file] [-TransformsSrc dir]
        [-BundleDir dir] [-AssetsSrc dir] [-EmbedZip file]

Steps: venv `desktop/.venv` (only used to run pip) -> `tools/bundle_decoder.py` (or `-BundleDir`) -> `make_backend.py`
-> stage `desktop/build/resources/{backend,decoder,runtime}` -> (unless `-SkipElectron`) `npm ci` + `npm run pack` in
`desktop/electron` -> LICENSES + `BUILD-INFO.json` -> `desktop/dist/ValoHub-<ver>-win64.zip` + `SHA256SUMS.txt`
-> `ValoHub.exe --check`.

* `bot.web.app` imports `dotenv`, so it is not shipped as an entry point; the sidecar must not import it.
* `bot/web/static/replays_app/assets/` is gitignored art; if the checkout lacks it pass `-AssetsSrc <dir>`.
* Python 3.12 only: the compiled wheels (`_multidict`, `yarl`, ...) and the .pyc files match the 3.12.10 runtime.
