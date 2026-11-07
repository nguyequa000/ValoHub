"""Single entry point of the frozen sidecar (``vrb.exe``): serve | normalize | --version | --self-test."""
from __future__ import annotations

import importlib
import json
import sys


def _self_test() -> int:
    report: dict = {"ok": True, "features": {}}
    try:
        from bot.replay import p2, paths
        for name in p2.FEATURES:
            try:
                importlib.import_module(f"bot.replay.{name}")
                report["features"][name] = "ok"
            except ModuleNotFoundError:
                report["features"][name] = "absent"
            except Exception as exc:  # noqa: BLE001
                report["features"][name] = f"error: {exc}"[:200]
                report["ok"] = False
        report["paths"] = paths.get_paths().to_dict()
        from bot.web import desktop
        report["version"] = desktop.APP_VERSION
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = str(exc)[:300]
    print(json.dumps(report, default=str), flush=True)
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: vrb serve --port P --data-dir D | normalize <raw> <viewer> | --version | --self-test")
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command == "--version":
        from bot.web.desktop import APP_VERSION
        print(APP_VERSION)
        return 0
    if command == "--self-test":
        return _self_test()
    if command == "serve":
        from bot.web import desktop
        return desktop.serve_main(rest)
    if command == "normalize":
        try:
            from bot.replay import childmain
        except ImportError:
            print("normalize unavailable: bot.replay.childmain is not present", file=sys.stderr)
            return 3
        result = childmain.run_normalize(rest)
        return result if isinstance(result, int) else 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
