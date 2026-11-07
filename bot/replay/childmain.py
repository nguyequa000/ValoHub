"""Child-process entry for the frozen app.

The frozen sidecar re-invokes itself as ``<exe> normalize <raw> <viewer>`` (alias
``--run-normalizer``).  This module is importable *before* any heavy module: ``normalize`` is
imported lazily inside ``run_normalize``.
"""

from __future__ import annotations

import sys

SUBCOMMANDS = ("normalize", "--run-normalizer")


def run_normalize(argv) -> int:
    from . import normalize  # lazy: keep this module cheap to import

    code = normalize.main(list(argv))
    return int(code or 0)


def maybe_run_child(argv=None) -> int | None:
    """Run a child subcommand if ``argv`` (default ``sys.argv[1:]``) starts with one; else None."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return run_normalize(argv[1:])
    return None
