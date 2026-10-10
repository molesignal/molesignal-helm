#!/usr/bin/env python3
"""Run the chart tests.

    python3 tests/run.py                # everything
    python3 tests/run.py -k secrets     # only scenario modules whose name contains "secrets"

Offline: needs `helm` and `yq` on PATH and never contacts a cluster. See harness.py.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import textwrap
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import results  # noqa: E402

MODULES = ["install", "secrets", "config", "options", "guards"]


def main() -> int:
    for tool in ("helm", "yq"):
        if not shutil.which(tool):
            print(f"{tool} is required on PATH")
            return 2
    pattern = None
    if "-k" in sys.argv:
        pattern = sys.argv[sys.argv.index("-k") + 1]
    for name in MODULES:
        if pattern and pattern not in name:
            continue
        module = importlib.import_module(f"scenarios_{name}")
        for scenario in module.SCENARIOS:
            try:
                scenario()
            except Exception:  # a crash is a failure, and must not hide the other scenarios
                results.failed += 1
                print(f"  ✗ {name}.{scenario.__name__} crashed\n" + textwrap.indent(traceback.format_exc(limit=-3), "      "))
    print(f"\n{results.passed} passed, {results.failed} failed")
    return 1 if results.failed else 0


if __name__ == "__main__":
    sys.exit(main())
