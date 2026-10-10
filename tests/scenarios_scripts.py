"""scripts/ci-install.sh, run against stand-ins for helm and kubectl.

The script needs a cluster; its control flow does not, and that is where scripts like this go
wrong: does a failed install still get its evidence dumped before the clean-up, does the next values
file still run, is the exit status right. The stand-ins record every call and fail on request."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from harness import ROOT, check, section

SHIM = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
tool="$(basename "$0")"
if [ "$tool" = helm ] && [ "$1" = install ] && [ "${FAKE_FAIL:-}" = install ]; then exit 1; fi
if [ "$tool" = helm ] && [ "$1" = test ] && [ "${FAKE_FAIL:-}" = test ]; then exit 1; fi
if [ "$tool" = kubectl ]; then
  case "$*" in
    *"get pods --output name"*) echo pod/one ;;
    *jsonpath*) echo "init app" ;;
  esac
fi
exit 0
"""


def run_script(fail: str = "") -> tuple[int, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        for tool in ("helm", "kubectl"):
            shim = bin_dir / tool
            shim.write_text(SHIM)
            shim.chmod(0o755)
        calls = Path(tmp) / "calls.log"
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls), "FAKE_FAIL": fail, "IMAGE_TAG": "sha-abc"}
        proc = subprocess.run([str(ROOT / "scripts" / "ci-install.sh")], cwd=ROOT, env=env, capture_output=True, text=True)
        return proc.returncode, calls.read_text().splitlines() if calls.exists() else []


def installs(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("helm install ")]


def ci_install() -> None:
    section("scripts · ci-install.sh, with stand-ins for helm and kubectl")
    ci_files = sorted(p.name for p in (ROOT / "charts" / "molesignal" / "ci").glob("*-values.yaml"))
    namespaces = [f"ci-{name.removesuffix('-values.yaml')}" for name in ci_files]

    status, calls = run_script()
    got = installs(calls)
    check(status == 0, "exits 0 when every install and test passes", f"{status}")
    check(len(got) == len(ci_files), f"installs once per values file ({len(ci_files)})", str(got))
    check(
        all("--set image.tag=sha-abc" in c and "--wait" in c and "--create-namespace" in c for c in got),
        "each install pins the application image tag from IMAGE_TAG and waits",
    )
    check(
        all(f"--values charts/molesignal/ci/{name}" in c for c, name in zip(got, ci_files)),
        "each install uses its own values file",
    )
    check(
        [c for c in calls if c.startswith("helm test ")] == [f"helm test ci --namespace {ns} --logs --timeout 5m" for ns in namespaces],
        "runs helm test after each install and shows its logs",
    )
    check(not any(" describe " in c or " logs " in c for c in calls if c.startswith("kubectl")), "dumps nothing when all is well")
    check(
        [c for c in calls if c.startswith("helm uninstall ")] and len([c for c in calls if c.startswith("kubectl delete namespace")]) == len(namespaces),
        "cleans up every release and namespace",
    )

    for what in ("install", "test"):
        status, calls = run_script(what)
        check(status == 1, f"exits 1 when {'an' if what == 'install' else 'a'} {what} fails")
        check(len(installs(calls)) == len(ci_files), f"a failed {what} does not stop the next values file")
        describe = [c for c in calls if c.startswith("kubectl") and " describe " in c]
        previous = [c for c in calls if c.startswith("kubectl") and " logs " in c and "--previous" in c]
        check(
            len(describe) == len(namespaces) and len(previous) >= len(namespaces),
            f"a failed {what} prints each pod's description and the previous run of every container",
            f"describe={len(describe)} previous={len(previous)}",
        )
        check(
            all(any(c.startswith("kubectl") and f"--container {c_}" in c for c in calls) for c_ in ("init", "app")),
            f"it asks for the init containers and the app containers",
        )
        dump_at = min(i for i, c in enumerate(calls) if c.startswith("kubectl") and " describe " in c)
        uninstall_at = min(i for i, c in enumerate(calls) if c.startswith("helm uninstall "))
        check(dump_at < uninstall_at, "the evidence is collected before the release is removed")


SCENARIOS = [ci_install]
