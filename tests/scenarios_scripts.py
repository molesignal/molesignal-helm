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
        [c for c in calls if c.startswith("helm test ")] == [f"helm test ci --namespace {ns} --timeout 5m" for ns in namespaces],
        "runs helm test after each install, without --logs, which fails once the passed test pod is gone",
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




# ── steps of the release workflow ─────────────────────────────────────────────


def workflow_step(name: str) -> str:
    """The `run:` script of a step of release.yaml, exactly as GitHub Actions would run it."""
    out = subprocess.run(
        ["yq", f'.jobs.release.steps[] | select(.name == "{name}") | .run', str(ROOT / ".github" / "workflows" / "release.yaml")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() and out.stdout.strip() != "null", f"no step named {name!r}"
    return out.stdout


def git(cwd: Path, *args: str, check_: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check_ and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def checkout_like_actions(tmp: Path, detached: bool) -> tuple[Path, Path]:
    """A bare 'remote' with a main branch, and a workspace checked out the way actions/checkout
    leaves it: on a local branch for a push event, detached for a pull request."""
    remote, work = tmp / "remote.git", tmp / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "charts" / "foo").mkdir(parents=True)
    (seed / "charts" / "foo" / "Chart.yaml").write_text("name: foo\n")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    git(work, "config", "user.name", "t")
    git(work, "config", "user.email", "t@t")
    if detached:
        git(work, "checkout", "-q", "--detach", "origin/main")
    return remote, work


def run_step(script: str, cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    # GitHub runs `run:` steps as `bash -e {0}`
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env={**os.environ, **env_extra}, capture_output=True, text=True)


def pages_branch() -> None:
    section("release workflow · creating the gh-pages branch (a push leaves a branch checked out, a pull request a detached HEAD)")
    script = workflow_step("Make sure the gh-pages branch exists")
    for detached in (False, True):
        label = "detached HEAD" if detached else "on a branch"
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            remote, work = checkout_like_actions(tmp, detached)
            before_head = git(work, "rev-parse", "HEAD")
            before_ref = git(work, "symbolic-ref", "--quiet", "--short", "HEAD", check_=False)
            proc = run_step(script, work, {"PAGES_DOMAIN": "charts.example.com"})
            check(proc.returncode == 0, f"{label}: the step succeeds", (proc.stdout + proc.stderr)[-400:])
            heads = git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads")
            check("gh-pages" in heads.split(), f"{label}: gh-pages is pushed", heads)
            if "gh-pages" in heads.split():
                files = git(remote, "ls-tree", "--name-only", "gh-pages").split()
                check(sorted(files) == [".nojekyll", "CNAME", "README.md"], f"{label}: it holds the README, the CNAME and .nojekyll", str(files))
                check(git(remote, "show", "gh-pages:CNAME") == "charts.example.com", f"{label}: the CNAME is the Pages domain")
                check(
                    git(remote, "rev-list", "--count", "gh-pages") == "1" and git(remote, "rev-list", "--parents", "-n1", "gh-pages").count(" ") == 0,
                    f"{label}: it is an orphan branch with one commit",
                )
            check(git(work, "rev-parse", "HEAD") == before_head, f"{label}: the workspace is back on the commit it started from")
            check(
                git(work, "symbolic-ref", "--quiet", "--short", "HEAD", check_=False) == before_ref,
                f"{label}: and on the same branch (or still detached)",
            )
            check((work / "charts" / "foo" / "Chart.yaml").exists(), f"{label}: the working tree has its files again (the chart is there to be released)")
            again = run_step(script, work, {"PAGES_DOMAIN": "charts.example.com"})
            check(again.returncode == 0 and "gh-pages exists" in again.stdout, f"{label}: a second run finds the branch and leaves it alone")
            check(git(remote, "rev-list", "--count", "gh-pages") == "1", f"{label}: without adding a commit")


def ghcr_step() -> None:
    section("release workflow · publishing to GHCR, with a stand-in for helm")
    script = workflow_step("Publish to GHCR (OCI)")
    shim = """#!/usr/bin/env bash
echo "helm $*" >> "$CALLS"
case "$1 $2" in
  "registry login") cat > /dev/null ;;
  "package "*) mkdir -p "$4" 2>/dev/null; : > "$4/foo-1.2.3.tgz" ;;
  "show chart") printf 'name: foo\\nversion: 1.2.3\\n' ;;
  "pull "*) [ "${FAKE_PUBLISHED:-}" = 1 ] || exit 1 ;;
esac
exit 0
"""
    for published in (False, True):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "bin").mkdir()
            (tmp / "bin" / "helm").write_text(shim)
            (tmp / "bin" / "helm").chmod(0o755)
            (tmp / "charts" / "foo").mkdir(parents=True)
            calls = tmp / "calls.log"
            env = {
                "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
                "CALLS": str(calls),
                "OWNER": "MoleSignal",
                "ACTOR": "someone",
                "TOKEN": "secret-token",
                "FAKE_PUBLISHED": "1" if published else "0",
            }
            proc = run_step(script, tmp, env)
            log = calls.read_text().splitlines() if calls.exists() else []
            label = "the version is already on ghcr.io" if published else "a new version"
            check(proc.returncode == 0, f"{label}: the step succeeds", (proc.stdout + proc.stderr)[-300:])
            check(any(c.startswith("helm registry login ghcr.io --username someone --password-stdin") for c in log), f"{label}: logs in to ghcr.io with the token on stdin, not on the command line")
            check(not any("secret-token" in c for c in log), f"{label}: the token never appears in a command")
            pushed = [c for c in log if c.startswith("helm push ")]
            if published:
                check(not pushed and "already on ghcr.io" in proc.stdout, f"{label}: leaves it alone")
            else:
                check(pushed == ["helm push dist/foo-1.2.3.tgz oci://ghcr.io/molesignal/charts"], f"{label}: pushes it, to the lower-cased owner", str(pushed))


SCENARIOS = [ci_install, pages_branch, ghcr_step]
