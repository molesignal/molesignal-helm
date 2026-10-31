"""Shared helpers for the chart tests: rendering, a small manifest model and the
integrity checks that every render has to pass.

Everything is offline. `KUBECONFIG` is pointed at /dev/null for every helm call, so
the tests cannot reach a real cluster even by accident. Requirements: `helm` and
`yq` (mikefarah) on PATH, Python 3.11+.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# These point the whole suite at another copy of a chart or of the install script; mutate.py
# uses them to check that the tests fail when one of those is broken.
CHART = Path(os.environ.get("MOLESIGNAL_CHART") or ROOT / "charts" / "molesignal").resolve()
PROBE_CHART = Path(os.environ.get("PROBE_AGENT_CHART") or ROOT / "charts" / "probe-agent").resolve()
CI_INSTALL = Path(os.environ.get("CI_INSTALL_SCRIPT") or ROOT / "scripts" / "ci-install.sh").resolve()
ENV = {**os.environ, "KUBECONFIG": os.devnull}

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
LABEL_VALUE = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")

WORKLOADS = ("Deployment", "StatefulSet")


class Results:
    """Counts passed and failed checks and prints them as it goes."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, cond: bool, msg: str, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  ✓ {msg}")
        else:
            self.failed += 1
            print(f"  ✗ {msg}" + (f"\n      {detail}" if detail else ""))
        return bool(cond)


results = Results()
check = results.check


def section(title: str) -> None:
    print(f"\n{title}")


# ── rendering ────────────────────────────────────────────────────────────────


class Rendered:
    """The outcome of one `helm template` call."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.docs: list[dict] = []
        if returncode == 0:
            parsed = subprocess.run(
                ["yq", "-o=json", "-I=0", "select(. != null)"],
                input=stdout,
                text=True,
                capture_output=True,
                check=True,
            )
            self.docs = [json.loads(line) for line in parsed.stdout.splitlines() if line.strip()]

    # selection
    def of(self, kind: str) -> list[dict]:
        return [d for d in self.docs if d["kind"] == kind]

    def get(self, kind: str, name: str) -> dict:
        for d in self.docs:
            if d["kind"] == kind and d["metadata"]["name"] == name:
                return d
        raise KeyError(f"{kind}/{name} not rendered; have {self.names(kind)}")

    def has(self, kind: str, name: str) -> bool:
        return any(d["kind"] == kind and d["metadata"]["name"] == name for d in self.docs)

    def names(self, kind: str) -> list[str]:
        return sorted(d["metadata"]["name"] for d in self.docs if d["kind"] == kind)

    def kinds(self) -> list[tuple[str, str]]:
        return sorted((d["kind"], d["metadata"]["name"]) for d in self.docs)

    # shortcuts
    def workloads(self) -> list[dict]:
        return [d for d in self.docs if d["kind"] in WORKLOADS]

    def config(self) -> dict:
        import tomllib

        cm = next(d for d in self.of("ConfigMap") if d["metadata"]["name"].endswith("-config"))
        return tomllib.loads(cm["data"]["config.toml"])

    def config_text(self) -> str:
        cm = next(d for d in self.of("ConfigMap") if d["metadata"]["name"].endswith("-config"))
        return cm["data"]["config.toml"]


def render(
    values: dict | None = None,
    sets: list[str] | None = None,
    *,
    release: str = "rel",
    namespace: str = "ns",
    chart: Path | str = CHART,
    extra: list[str] | None = None,
) -> Rendered:
    """Run `helm template`. `values` is passed as a values file (numbers then reach
    the templates as float64, exactly as they do from a user's values.yaml)."""
    cmd = ["helm", "template", release, str(chart), "--namespace", namespace]
    tmp: Path | None = None
    try:
        if values is not None:
            fd, name = tempfile.mkstemp(suffix=".json", prefix="values-")
            tmp = Path(name)
            with os.fdopen(fd, "w") as fh:
                json.dump(values, fh)
            cmd += ["--values", name]
        for s in sets or []:
            cmd += ["--set", s]
        cmd += extra or []
        proc = subprocess.run(cmd, env=ENV, capture_output=True, text=True)
        return Rendered(proc.returncode, proc.stdout, proc.stderr)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def refuses(msg: str, expect: str, values: dict | None = None, sets: list[str] | None = None, *, chart: Path | str = CHART) -> None:
    """A render that must fail, with `expect` in the message."""
    out = render(values, sets, chart=chart)
    check(
        out.returncode != 0 and expect in out.stderr,
        msg,
        f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]!r}",
    )


@contextmanager
def chart_variant(*, fake_lookup: bool = False, notes_preview: bool = False, source: Path = CHART):
    """A temporary copy of a chart, modified for testing.

    fake_lookup   `lookup` cannot run offline, so the single call that reads an
                  existing Secret is replaced by a read from `.Values.__fakeSecrets`
                  (name -> {data: {key: base64}}). The reuse logic around it runs
                  unchanged. The schema is dropped because it rejects the extra key.
    notes_preview adds a ConfigMap that carries the rendered NOTES text.
    """
    tmp = Path(tempfile.mkdtemp(prefix="chart-variant-"))
    try:
        copy = tmp / source.name
        shutil.copytree(source, copy)
        if fake_lookup:
            helpers = copy / "templates" / "_helpers.tpl"
            text = helpers.read_text()
            real = '{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace .name -}}'
            fake = "{{- $existing := get (default (dict) .ctx.Values.__fakeSecrets) .name | default (dict) -}}"
            assert text.count(real) == 1, "the lookup call in secretData changed; update the test hook"
            helpers.write_text(text.replace(real, fake))
            (copy / "values.schema.json").unlink()
        if notes_preview:
            (copy / "templates" / "notes-preview.yaml").write_text(
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: notes-preview\n"
                f'data:\n  notes: {{{{ include "{source.name}.notes" . | toJson }}}}\n'
            )
        yield copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode()).decode()


def unb64(text: str) -> str:
    import base64

    return base64.b64decode(text).decode()


# ── a small manifest model ───────────────────────────────────────────────────


def pod_spec(doc: dict) -> dict:
    if doc["kind"] in WORKLOADS:
        return doc["spec"]["template"]["spec"]
    if doc["kind"] == "Pod":
        return doc["spec"]
    raise ValueError(doc["kind"])


def pod_labels(doc: dict) -> dict:
    if doc["kind"] in WORKLOADS:
        return doc["spec"]["template"]["metadata"].get("labels", {})
    return doc["metadata"].get("labels", {})


def containers(doc: dict, *, init: bool = False) -> list[dict]:
    spec = pod_spec(doc)
    return spec.get("initContainers", []) if init else spec.get("containers", [])


def env_of(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def container(doc: dict, name: str) -> dict:
    for c in containers(doc) + containers(doc, init=True):
        if c["name"] == name:
            return c
    raise KeyError(name)


def port_names(container_: dict) -> set[str]:
    return {p["name"] for p in container_.get("ports", []) if "name" in p}


# ── integrity: every render has to pass these ────────────────────────────────


def integrity(m: Rendered, label: str) -> None:
    """Cross-reference checks that no schema validation would catch."""
    docs = m.docs
    problems: list[str] = []

    def problem(text: str) -> None:
        problems.append(text)

    secrets = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Secret"}
    configmaps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "ConfigMap"}
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    workloads = m.workloads()

    seen: set[tuple[str, str]] = set()
    for d in docs:
        ident = (d["kind"], d["metadata"]["name"])
        if ident in seen:
            problem(f"{ident} rendered twice")
        seen.add(ident)
        name = d["metadata"]["name"]
        if not DNS_LABEL.match(name) or len(name) > 63:
            problem(f"{ident}: not a valid DNS label")
        for k, v in {**d["metadata"].get("labels", {}), **pod_labels(d)}.items():
            if not LABEL_VALUE.match(str(v)) or len(str(v)) > 63:
                problem(f"{ident}: label {k}={v!r} is not valid")

    for w in workloads:
        ident = f"{w['kind']}/{w['metadata']['name']}"
        sel = w["spec"]["selector"]["matchLabels"]
        labels = pod_labels(w)
        if not sel.items() <= labels.items():
            problem(f"{ident}: selector {sel} is not a subset of the pod labels {labels}")
        if w["kind"] == "StatefulSet":
            if w["spec"]["serviceName"] not in services:
                problem(f"{ident}: serviceName {w['spec']['serviceName']!r} is not rendered")
            if len(w["metadata"]["name"]) + 3 > 63:
                problem(f"{ident}: pod names would exceed 63 characters")
        spec = pod_spec(w)
        volumes = {v["name"] for v in spec.get("volumes", [])}
        volumes |= {t["metadata"]["name"] for t in w["spec"].get("volumeClaimTemplates", [])}
        for c in containers(w) + containers(w, init=True):
            for vm in c.get("volumeMounts", []):
                if vm["name"] not in volumes:
                    problem(f"{ident}/{c['name']}: mounts undefined volume {vm['name']!r}")
            names = port_names(c)
            for probe_key in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(probe_key)
                port = (probe or {}).get("httpGet", {}).get("port")
                if isinstance(port, str) and port not in names:
                    problem(f"{ident}/{c['name']}: {probe_key} uses unknown port {port!r}")
            defined: set[str] = set()
            for e in c.get("env", []):
                for ref in re.findall(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", e.get("value", "")):
                    if ref not in defined:
                        problem(f"{ident}/{c['name']}: env {e['name']} uses $({ref}) before it is defined")
                defined.add(e["name"])
                ref = e.get("valueFrom", {}).get("secretKeyRef")
                if ref and ref["name"] in secrets and not ref.get("optional"):
                    if ref["key"] not in secrets[ref["name"]].get("data", {}):
                        problem(f"{ident}/{c['name']}: secret {ref['name']} has no key {ref['key']!r}")
        for v in spec.get("volumes", []):
            cm = v.get("configMap")
            if cm and cm["name"] not in configmaps:
                problem(f"{ident}: volume {v['name']} uses ConfigMap {cm['name']!r} that is not rendered")

    for name, svc in services.items():
        sel = svc["spec"].get("selector", {})
        targets = [w for w in workloads if sel.items() <= pod_labels(w).items()]
        if len(targets) != 1:
            problem(f"Service/{name}: selects {len(targets)} workloads, expected exactly 1")
            continue
        names = set().union(*(port_names(c) for c in containers(targets[0])))
        for p in svc["spec"].get("ports", []):
            tp = p.get("targetPort")
            if isinstance(tp, str) and tp not in names:
                problem(f"Service/{name}: targetPort {tp!r} is not a port of {targets[0]['metadata']['name']}")

    for hpa in m.of("HorizontalPodAutoscaler"):
        ref = hpa["spec"]["scaleTargetRef"]
        if not m.has(ref["kind"], ref["name"]):
            problem(f"HPA/{hpa['metadata']['name']}: target {ref} is not rendered")
        else:
            target = m.get(ref["kind"], ref["name"])
            if "replicas" in target["spec"]:
                problem(f"{ref['name']}: sets replicas although an HPA owns the count")

    for pdb in m.of("PodDisruptionBudget"):
        sel = pdb["spec"]["selector"]["matchLabels"]
        if not any(sel.items() <= pod_labels(w).items() for w in workloads):
            problem(f"PDB/{pdb['metadata']['name']}: selects no workload")

    for ing in m.of("Ingress"):
        for rule in ing["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                backend = path["backend"]["service"]
                svc = services.get(backend["name"])
                if svc is None:
                    problem(f"Ingress/{ing['metadata']['name']}: backend {backend['name']!r} is not rendered")
                    continue
                port = backend["port"]
                have = svc["spec"]["ports"]
                ok = any(p["name"] == port.get("name") for p in have) if "name" in port else any(
                    p["port"] == port.get("number") for p in have
                )
                if not ok:
                    problem(f"Ingress/{ing['metadata']['name']}: port {port} is not on Service {backend['name']}")

    check(not problems, f"{label}: manifests are consistent ({len(docs)} objects)", "\n      ".join(problems))
