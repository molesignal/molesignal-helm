#!/usr/bin/env python3
"""Write the values table of each chart's README from its values.yaml.

A comment that starts with `# --` documents the key right below it (a following
`# ` line continues it). A documented key becomes one row and its children are
skipped; an undocumented key with children is walked into; an undocumented leaf is
an error, so a new value cannot ship without a description.

    python3 scripts/gen-values-doc.py           # rewrite the table in README.md
    python3 scripts/gen-values-doc.py --check   # exit 1 if README.md is out of date

Needs `yq` (mikefarah) on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHARTS = sorted(path.parent for path in (ROOT / "charts").glob("*/Chart.yaml"))
START, END = "<!-- values:start -->", "<!-- values:end -->"


def load_values(chart: Path) -> dict:
    out = subprocess.run(["yq", "-o=json", ".", str(chart / "values.yaml")], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def read_docs(text: str) -> dict[tuple[str, ...], str]:
    """Map the path of every documented key to its description."""
    docs: dict[tuple[str, ...], str] = {}
    stack: list[tuple[int, str]] = []
    pending: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# --"):
            pending = [stripped[len("# --") :].strip()]
            continue
        if pending is not None and stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+):(?:\s|$)", line)
        if match and not stripped.startswith(("#", "-")):
            indent = len(match.group(1))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, match.group(2)))
            if pending is not None:
                docs[tuple(key for _, key in stack)] = " ".join(p for p in pending if p)
        pending = None
    return docs


def kind(value: object) -> str:
    return {str: "string", bool: "bool", int: "int", float: "number", list: "list", dict: "object", type(None): "null"}[type(value)]


def show(value: object) -> str:
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "..."


def rows(values: dict, docs: dict[tuple[str, ...], str]) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    table: list[tuple[str, str, str, str]] = []
    missing: list[str] = []

    def walk(obj: object, path: tuple[str, ...]) -> None:
        if path in docs:
            table.append((".".join(path), kind(obj), show(obj), docs[path]))
        elif isinstance(obj, dict) and obj:
            for key, child in obj.items():
                walk(child, path + (key,))
        else:
            missing.append(".".join(path))

    for key, child in values.items():
        walk(child, (key,))
    return table, missing


def render(table: list[tuple[str, str, str, str]]) -> str:
    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines = ["| Key | Type | Default | Description |", "| --- | --- | --- | --- |"]
    for key, type_, default, description in table:
        lines.append(f"| `{key}` | {type_} | `{cell(default)}` | {cell(description)} |")
    return "\n".join(lines)


def update(chart: Path) -> int:
    readme = chart / "README.md"
    values = load_values(chart)
    docs = read_docs((chart / "values.yaml").read_text())
    table, missing = rows(values, docs)
    if missing:
        print(f"values of {chart.name} without a `# --` description:\n  " + "\n  ".join(missing))
        return 1
    text = readme.read_text()
    if START not in text or END not in text:
        print(f"{readme} needs the markers {START} and {END}")
        return 1
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = f"{head}{START}\n{render(table)}\n{END}{tail}"
    if "--check" in sys.argv:
        if updated != text:
            print(f"charts/{chart.name}/README.md is out of date; run: python3 scripts/gen-values-doc.py")
            return 1
        print(f"{chart.name}: README values table is up to date ({len(table)} rows)")
        return 0
    readme.write_text(updated)
    print(f"{chart.name}: wrote {len(table)} rows")
    return 0


def main() -> int:
    return max(update(chart) for chart in CHARTS)


if __name__ == "__main__":
    sys.exit(main())
