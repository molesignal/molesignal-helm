"""scripts/ci-install.sh, run against stand-ins for helm, kubectl and the server's API.

The script needs a cluster; its control flow does not, and that is where scripts like this go
wrong: does a failed install still get its evidence dumped before the clean-up, does the next values
file still run, is the exit status right. The stand-ins record every call and fail on request."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from harness import CI_INSTALL, CHART, PROBE_CHART, ROOT, check, section

SHIM = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
tool="$(basename "$0")"
if [ "$tool" = helm ] && [ "$1" = install ] && [ "${FAKE_FAIL:-}" = install ]; then exit 1; fi
if [ "$tool" = helm ] && [ "$1" = test ] && [ "${FAKE_FAIL:-}" = test ]; then exit 1; fi
if [ "$tool" = helm ] && [ "$1" = install ] && [ "$2" != ci ] && [ "${FAKE_FAIL:-}" = agent-install ]; then exit 1; fi
if [ "$tool" = kubectl ]; then
  case "$*" in
    *"get pods --output name"*) echo pod/one ;;
    *"get statefulset/agent-token-"*) printf 2 ;;
    *"get statefulset/"*) printf 1 ;;
    *"--output jsonpath"*) echo "init app" ;;
    *"logs "*"--container register"*) [ "${FAKE_FAIL:-}" = lost-identity ] && echo "registering with ..." || echo "already registered: using the identity in /state" ;;
  esac
fi
exit 0
"""

# The server's API, on the far side of curl. The state is the number of the call, which is all a
# heartbeat needs in order to move, and whether a pod was deleted (the stand-in for kubectl records it).
CURL_SHIM = """#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
url = args[-1]
method = args[args.index("--request") + 1]
headers = [args[i + 1] for i, a in enumerate(args) if a == "--header"]
data = args[args.index("--data") + 1] if "--data" in args else ""
path = url.split("/api/v1", 1)[1]
fail = os.environ.get("FAKE_FAIL", "")
compact = json.dumps(json.loads(data), separators=(",", ":")) if data else ""
with open(os.environ["CALLS"], "a") as log:
    log.write(f"api {method} {path} {compact} {' '.join(h for h in headers if h.startswith('Authorization'))}\\n")
with open(os.environ["CALLS"]) as log:
    calls = log.read().splitlines()

CA = "-----BEGIN CERTIFICATE-----\\nfrom the server\\n-----END CERTIFICATE-----\\n"

def answer(value):
    print(json.dumps(value))
    sys.exit(0)

def refuse(message):
    print(json.dumps({"error": message}))
    sys.exit(22)

if path == "/healthz":
    answer({"status": "ok"})
if path == "/auth/signin":
    if fail == "signin":
        refuse("wrong password")
    answer({"token": "session-token"} if fail != "cookies" else {"login": {}})
if path == "/synthetics/locations":
    answer({"id": "location-" + json.loads(data)["code"]})
if path.endswith("/register-tokens"):
    answer({"register_token": "one-time-secret", "ca_certificate_pem": CA})
if path == "/synthetics/agent-tokens":
    answer({"agent_token": "ms_probe_agent-secret", "ca_certificate_pem": CA})
if path.endswith("/agents"):
    kind = path.split("/")[3].removeprefix("location-")
    count = 2 if kind == "agent-token" else 1
    deleted = any(c.startswith("kubectl") and "delete pod" in c and kind in c for c in calls)
    status = "registered" if fail == "offline" else "online"
    if fail == "stale" and deleted:
        status = "offline"
    agents = [
        {
            "id": f"{kind}-{i}" + ("-new" if fail == "new-identity" and deleted else ""),
            "hostname": f"{kind}-probe-agent-{i}",
            "status": status,
            "capabilities": ["http", "browser"],
            "last_heartbeat_at": 1000 + (len(calls) if fail != "stuck-heartbeat" else 0),
        }
        for i in range(count)
    ]
    if fail == "shared-identity":
        for agent in agents:
            agent["id"] = "same"
    answer(agents)
if path == "/synthetics/monitors":
    name = json.loads(data)["name"]
    answer({"monitor": {"id": f"monitor-{name}"}, "revision": {"id": "revision-1"}})
if path.endswith("/test"):
    answer([])
if "/results" in path:
    bad = fail == "browser-check" and "monitor-browser" in path
    answer([{"outcome": "failing" if bad else "healthy", "agent_id": "agent-1"}])
refuse("no such route: " + path)
"""


def run_script(fail: str = "", charts: tuple[str, ...] = ("molesignal",)) -> tuple[int, list[str], str]:
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        for tool, text in (("helm", SHIM), ("kubectl", SHIM), ("curl", CURL_SHIM)):
            shim = bin_dir / tool
            shim.write_text(text)
            shim.chmod(0o755)
        calls = Path(tmp) / "calls.log"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "FAKE_FAIL": fail,
            "IMAGE_TAG": "sha-abc",
            "POLL_SECONDS": "0",
            "POLL_ATTEMPTS": "3",
        }
        proc = subprocess.run([str(CI_INSTALL), *charts], cwd=ROOT, env=env, capture_output=True, text=True)
        return proc.returncode, calls.read_text().splitlines() if calls.exists() else [], proc.stdout + proc.stderr


def installs(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("helm install ")]


def ci_install() -> None:
    section("scripts · ci-install.sh, with stand-ins for helm and kubectl")
    ci_files = sorted(p.name for p in (ROOT / "charts" / "molesignal" / "ci").glob("*-values.yaml"))
    namespaces = [f"ci-{name.removesuffix('-values.yaml')}" for name in ci_files]

    status, calls, _ = run_script()
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
        status, calls, _ = run_script(what)
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


def ci_install_probe_agent() -> None:
    section("scripts · ci-install.sh for probe-agent: a server, a token, Agents that come online and stay themselves")
    files = sorted(p.name for p in (PROBE_CHART / "ci").glob("*-values.yaml"))
    kinds = [name.removesuffix("-values.yaml") for name in files]
    ca = base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfrom the server\n-----END CERTIFICATE-----\n").decode()

    status, calls, output = run_script(charts=("probe-agent",))
    check(status == 0, "exits 0 when everything passes", output[-600:])
    got = installs(calls)
    check(
        got[0].startswith("helm install ci charts/molesignal ")
        and "--values charts/probe-agent/ci/molesignal-server.yaml" in got[0]
        and "--set image.tag=sha-abc" in got[0]
        and "--wait" in got[0],
        "installs the molesignal chart first, as the server, and waits for it",
        str(got[:1]),
    )
    secret_at = next(i for i, c in enumerate(calls) if "create secret generic ci-root" in c)
    check(secret_at < calls.index(got[0]), "the root password's Secret exists before the server starts")
    signin = [c for c in calls if c.startswith("api POST /auth/signin")]
    check(len(signin) == 1 and calls.index(signin[0]) > calls.index(got[0]), "signs in once, after the server is up")
    check(any("port-forward svc/ci-molesignal" in c for c in calls), "reaches it through a port-forward")

    agent_installs = got[1:]
    check([c.split()[2] for c in agent_installs] == kinds, "installs one release per values file", str(agent_installs))
    for kind, name in zip(kinds, files):
        install = next(c for c in agent_installs if c.split()[2] == kind)
        check(
            f"--values charts/probe-agent/ci/{name}" in install and "--set image.tag=sha-abc" in install and "--wait" in install and "charts/probe-agent" in install.split()[3],
            f"{kind}: uses its own values file, the image tag from IMAGE_TAG, and waits",
        )
        check(f"--set-string registration.caCertificateBase64={ca}" in install, f"{kind}: with the CA the server generated")
        location = next(i for i, c in enumerate(calls) if c.startswith("api POST /synthetics/locations ") and kind in c)
        secret = next(i for i, c in enumerate(calls) if f"create secret generic ci-{kind}" in c)
        check(location < secret < calls.index(install), f"{kind}: location, then the token's Secret, then the install")
        check(f"helm test {kind} --namespace ci-probe-agent --timeout 5m" in calls, f"{kind}: runs helm test")
        polled = next(i for i, c in enumerate(calls) if c.startswith(f"api GET /synthetics/locations/location-{kind}/agents"))
        check(calls.index(install) < polled, f"{kind}: asks the server for its Agents after the install")
        deleted = next(i for i, c in enumerate(calls) if f"delete pod {kind}-probe-agent-0" in c)
        check(polled < deleted, f"{kind}: deletes a pod once the Agents are online")
        check(any(f"logs {kind}-probe-agent-0 --container register" in c for c in calls[deleted:]), f"{kind}: and reads what its init container found")
    one_time = next(c for c in calls if "create secret generic ci-one-time-token" in c)
    agent = next(c for c in calls if "create secret generic ci-agent-token" in c)
    check("token=one-time-secret" in one_time and "token=ms_probe_agent-secret" in agent, "each kind of token goes into its own Secret")
    check(any("/register-tokens" in c for c in calls) and any("/synthetics/agent-tokens" in c for c in calls), "asks the server for both kinds")
    check(not any("one-time-secret" in c or "ms_probe_agent-secret" in c for c in calls if c.startswith("helm ")), "the token is never on a helm command line")
    prefix = "api POST /synthetics/monitors "
    monitors = [json.loads(c[len(prefix):].split(" Authorization")[0]) for c in calls if c.startswith(prefix)]
    check(sorted(m["spec"]["kind"] for m in monitors) == sorted(["http", "browser"] * len(kinds)), "runs an HTTP and a browser check per values file", str(monitors))
    check(all(m["location_ids"] and m["spec"]["configuration"]["steps"] for m in monitors), "each in the location of its Agents")
    check(
        all("ci-molesignal.ci-probe-agent.svc.cluster.local:5080" in json.dumps(m["spec"]) for m in monitors),
        "against the server, inside the cluster",
    )
    check(any(c.startswith("api POST /synthetics/locations ") and '"allow_private_networks":true' in c for c in calls), "from a location that may reach private addresses")
    check(not any(" describe " in c for c in calls if c.startswith("kubectl")), "dumps nothing when all is well")
    check(
        all(f"helm uninstall {name} --namespace ci-probe-agent --wait --timeout 5m" in calls for name in [*kinds, "ci"])
        and any(c.startswith("kubectl delete namespace ci-probe-agent") for c in calls),
        "cleans up every release and the namespace",
    )
    check(all("Authorization: Bearer session-token" in c for c in calls if c.startswith("api POST /synthetics/")), "uses the session token up to application 26.2.0.0")

    status, calls, output = run_script("cookies", ("probe-agent",))
    check(status == 0, "works when signing in sets a cookie instead, as later versions do", output[-400:])
    check(not any("Authorization" in c for c in calls), "and then sends no Authorization header")

    for fail, what in (
        ("signin", "cannot sign in"),
        ("agent-install", "an Agent does not install"),
        ("offline", "Agents never come online"),
        ("shared-identity", "two replicas share an identity"),
        ("browser-check", "a browser check does not come back healthy"),
        ("new-identity", "a deleted pod comes back as a new Agent"),
        ("lost-identity", "a deleted pod registers again"),
        ("stuck-heartbeat", "a deleted pod's Agent does not come back"),
        ("test", "helm test fails"),
    ):
        status, calls, output = run_script(fail, ("probe-agent",))
        check(status == 1, f"exits 1 when {what}", output[-400:])
        described = [i for i, c in enumerate(calls) if c.startswith("kubectl") and " describe " in c]
        removed = [i for i, c in enumerate(calls) if c.startswith("helm uninstall ")]
        check(described and min(described) < min(removed), f"and prints the evidence before the clean-up when {what}")
        check(any(c.startswith("kubectl delete namespace ci-probe-agent") for c in calls), f"and still cleans up when {what}")

    status, calls, _ = run_script("browser-check", ("probe-agent",))
    check(
        all(any(c.startswith("helm install ") and c.split()[2] == kind for c in calls) for kind in kinds),
        "a failed values file does not stop the next one",
    )

    status, calls, _ = run_script(charts=())
    got = installs(calls)
    molesignal_files = sorted(p.name for p in (CHART / "ci").glob("*-values.yaml"))
    check(status == 0 and len(got) == len(molesignal_files) + 1 + len(kinds), "with no argument it installs both charts", str(got))
    status, _, output = run_script(charts=("no-such-chart",))
    check(status == 1 and "unknown chart" in output, "refuses a chart it does not know")


def ci_values_agree() -> None:
    section("scripts · the ci/ files of probe-agent and the script describe the same server")
    server = json.loads(subprocess.run(["yq", "-o=json", ".", str(PROBE_CHART / "ci" / "molesignal-server.yaml")], capture_output=True, text=True, check=True).stdout)
    host = "ci-molesignal.ci-probe-agent.svc.cluster.local"
    check(server["config"]["probe"]["register_endpoint"] == f"https://{host}:5084", "the server prints its Service's name, in the namespace of the script, as its register endpoint")
    check(server["config"]["probe"]["control_endpoint"] == f"https://{host}:5085", "and as its control endpoint")
    check(server["config"]["probe"]["server_names"] == [host], "and has a certificate for that name")
    environment = {e["name"]: e for e in server["extraEnv"]}
    check(environment["MS_AUTH_ROOT_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "ci-root", "the root password comes from the Secret the script creates, not from the ConfigMap")
    script = CI_INSTALL.read_text()
    check(f'server_email="{environment["MS_AUTH_ROOT_EMAIL"]["value"]}"' in script, "the script signs in as that user")
    for path in sorted((PROBE_CHART / "ci").glob("*-values.yaml")):
        kind = path.name.removesuffix("-values.yaml")
        values = json.loads(subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True).stdout)
        check(values["registration"]["endpoint"] == f"https://{host}:5084", f"{kind}: registers with that server")
        check(values["registration"]["existingSecret"] == f"ci-{kind}", f"{kind}: reads the Secret the script creates")
        check(f"{kind})" in script, f"{kind}: the script knows which token that file wants")
    two = json.loads(subprocess.run(["yq", "-o=json", ".", str(PROBE_CHART / "ci" / "agent-token-values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(two["replicaCount"] == 2, "the Agent Token is tried with two replicas, each of which has to register")


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
  "package "*) mkdir -p "$4" 2>/dev/null; name="$(basename "$2")"; : > "$4/${name}-1.2.3.tgz" ;;
  "show chart") name="$(basename "$3")"; printf 'name: %s\\nversion: 1.2.3\\n' "${name%-1.2.3.tgz}" ;;
  "pull "*) case "$2" in *"/charts/old") ;; *"/charts/new") exit 1 ;; *) [ "${FAKE_PUBLISHED:-}" = 1 ] || exit 1 ;; esac ;;
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

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "bin").mkdir()
        (tmp / "bin" / "helm").write_text(shim)
        (tmp / "bin" / "helm").chmod(0o755)
        for name in ("old", "new"):
            (tmp / "charts" / name).mkdir(parents=True)
        calls = tmp / "calls.log"
        env = {"PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "CALLS": str(calls), "OWNER": "molesignal", "ACTOR": "someone", "TOKEN": "t"}
        proc = run_step(script, tmp, env)
        log = calls.read_text().splitlines()
        check(proc.returncode == 0, "two charts: the step succeeds", (proc.stdout + proc.stderr)[-300:])
        check(
            sorted(c for c in log if c.startswith("helm package ")) == ["helm package charts/new/ --destination dist", "helm package charts/old/ --destination dist"],
            "two charts: packages each one",
        )
        check(
            [c for c in log if c.startswith("helm push ")] == ["helm push dist/new-1.2.3.tgz oci://ghcr.io/molesignal/charts"]
            and "old 1.2.3 is already on ghcr.io" in proc.stdout,
            "two charts: pushes the one with a new version and leaves the other alone, so each is released on its own",
            "\n".join(log),
        )


def release_each_chart() -> None:
    section("release workflow · every chart in charts/ is released under its own name")
    charts = sorted(p.parent.name for p in (ROOT / "charts").glob("*/Chart.yaml"))
    check(charts == ["molesignal", "probe-agent"], "both charts are in charts/", str(charts))
    config = subprocess.run(["yq", '.["release-name-template"]', str(ROOT / "cr.yaml")], capture_output=True, text=True, check=True).stdout.strip()
    check(config == "{{ .Name }}-{{ .Version }}", "releases are named after the chart and its version, so versions of two charts cannot collide", config)
    chart_dirs = subprocess.run(["yq", '.["chart-dirs"][0]', str(ROOT / "ct.yaml")], capture_output=True, text=True, check=True).stdout.strip()
    check(chart_dirs == "charts", "chart-testing looks at every chart")
    workflow = (ROOT / ".github" / "workflows" / "release.yaml").read_text()
    check('for chart in charts/*/; do' in workflow and "helm/chart-releaser-action" in workflow and "skip_existing: true" in workflow, "the workflow covers every chart, and skips a version that exists")
    versions = {name: subprocess.run(["yq", ".version", str(ROOT / "charts" / name / "Chart.yaml")], capture_output=True, text=True, check=True).stdout.strip() for name in charts}
    changelog = (ROOT / "CHANGELOG.md").read_text()
    for name, version in versions.items():
        check(f"## [{name} {version}]" in changelog, f"the changelog has an entry for {name} {version}")


SCENARIOS = [ci_install, ci_install_probe_agent, ci_values_agree, pages_branch, ghcr_step, release_each_chart]
