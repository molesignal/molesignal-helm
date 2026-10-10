"""Guards (combinations that must be refused), the install notes, chart metadata,
`helm lint` over every shipped values file, and the packaged archive."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from harness import CHART, ENV, ROOT, chart_variant, check, refuses, render, section

FULL = "rel-molesignal"
EXTERNAL = {
    "postgresql": {"enabled": False},
    "rustfs": {"enabled": False},
    "externalDatabase": {"dsn": "postgres://u:p@db:5432/m"},
}




def guards() -> None:
    section("guards · combinations the chart refuses, with a message that says what to change")
    refuses("an unknown mode", "must be one of", sets=["mode=cluster"])
    refuses("PostgreSQL bundled and external at once", "Choose one", {"externalDatabase": {"dsn": "postgres://x"}})
    refuses(
        "no database at all",
        "no metadata database",
        {"postgresql": {"enabled": False}},
    )
    refuses("a bad database user", "`postgresql.auth.username`", {"postgresql": {"auth": {"username": "bad-user"}}})
    refuses("a bad database name", "`postgresql.auth.database`", {"postgresql": {"auth": {"database": "a b"}}})
    refuses("a password that would break the connection string", "embedded in the connection string", {"postgresql": {"auth": {"password": "p@ss/word"}}})
    refuses("RustFS bundled and an endpoint at once", "Choose one", {"objectStore": {"endpoint": "https://s3.example.com"}})
    refuses("RustFS bundled and keys at once", "Choose one", {"objectStore": {"accessKey": "a", "secretKey": "b"}})
    refuses("RustFS in another region", "us-east-1", {"objectStore": {"region": "eu-west-1"}})
    refuses("half a key pair", "must be set together", {**EXTERNAL, "rustfs": {"enabled": False}, "objectStore": {"accessKey": "only"}})
    refuses("a key pair and an existing secret", "not both", {**EXTERNAL, "objectStore": {"accessKey": "a", "secretKey": "b", "existingSecret": "x"}})
    refuses("no bucket", "`objectStore.bucket` is required", {"objectStore": {"bucket": ""}})
    refuses("the web layer without an image", "no web image is published", {"mode": "distributed", "web": {"enabled": True}})
    refuses("the web layer in standalone mode", "needs `mode: distributed`", {"web": {"enabled": True, "image": {"repository": "r/w"}}})
    refuses("autoscaling with nothing to scale on", "no CPU or memory target", {"mode": "distributed", "router": {"autoscaling": {"enabled": True, "targetCPUUtilizationPercentage": None}}})
    refuses("a typo in a top-level key", "Additional property ingres is not allowed", sets=["ingres.enabled=true"])
    refuses("a value of the wrong type", "Expected: integer", sets=["router.replicaCount=many"])
    refuses("an invalid release channel", "must be one of", sets=["releaseChannel=prod"])
    refuses("an invalid service type", "must be one of", sets=["router.service.type=Bogus"])
    refuses("a malformed digest", "digest", sets=["image.digest=latest"])
    refuses("a storage size that is not a quantity", "size", sets=["standalone.persistence.size=lots"])
    ok = render({**EXTERNAL, "objectStore": {"existingSecret": "x"}})
    check(ok.returncode == 0, "an existing object-store secret needs no inline keys", ok.stderr)


def notes() -> None:
    section("notes · the text helm install prints")
    with chart_variant(notes_preview=True) as chart:

        def text(values: dict | None = None) -> str:
            out = render(values, chart=chart)
            assert out.returncode == 0, out.stderr
            return out.get("ConfigMap", "notes-preview")["data"]["notes"]

        base = text()
        check("helm test rel --namespace ns" in base, "tells you how to test")
        check("port-forward svc/rel-molesignal 5080:5080" in base, "tells you how to reach the UI without an ingress")
        check("rel-molesignal.ns.svc.cluster.local:4317" in base, "gives the in-cluster OTLP endpoint")
        check("Evaluation setup" in base and "PostgreSQL and RustFS are" in base, "warns about the bundled services")
        check("get secret rel-molesignal-secret" in base and "cipher-key" in base, "shows how to back up the cipher key")
        check("9001:9001" in base and "rel-molesignal-rustfs" in base, "points to the RustFS console")
        check("helm template" in base, "warns about GitOps rendering while secrets are generated")

        dist = text({"mode": "distributed", "ingress": {"enabled": True, "hosts": [{"host": "obs.example.com", "paths": [{"path": "/"}]}], "tls": [{"secretName": "t"}]}})
        check(
            "https://obs.example.com" in dist and "port-forward svc/rel-molesignal-router" not in dist,
            "lists the ingress host instead of a port-forward to the UI",
        )
        check("rel-molesignal-router.ns.svc.cluster.local:4317" in dist, "distributed OTLP goes to the router")

        prod = text({**EXTERNAL, "objectStore": {"accessKey": "a", "secretKey": "b"}, "secrets": {"cipherKey": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}})
        check("Evaluation setup" not in prod and "9001" not in prod, "no bundled-service notes when they are off")
        check("helm template" not in prod, "no GitOps warning when secrets are set explicitly")
        only_pg = text({"rustfs": {"enabled": False}, "objectStore": {"endpoint": "https://s3.example.com", "accessKey": "a", "secretKey": "b"}})
        check("the bundled PostgreSQL is one instance" in only_pg.replace("\n   ", " "), "names only the service that is bundled")


def metadata() -> None:
    section("metadata · Chart.yaml and values agree")
    chart = json.loads(subprocess.run(["yq", "-o=json", ".", str(CHART / "Chart.yaml")], capture_output=True, text=True, check=True).stdout)
    values = json.loads(subprocess.run(["yq", "-o=json", ".", str(CHART / "values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", chart["version"]) is not None, f"version {chart['version']} is SemVer")
    check(isinstance(chart["appVersion"], str) and chart["appVersion"], "appVersion is a string")
    check(
        chart["appVersion"] == "latest" and values["image"]["tag"] == "",
        "the application image follows latest, through appVersion",
    )
    check(
        values["rustfs"]["image"]["tag"] == "latest" and values["rustfs"]["bucketInit"]["image"]["tag"] == "latest",
        "RustFS and the AWS CLI follow latest",
    )
    check(values["postgresql"]["image"]["tag"] == "17", "PostgreSQL stays on 17")
    images = chart["annotations"]["artifacthub.io/images"]
    expect = {
        f"{values['image']['repository']}:{chart['appVersion']}": "the application image at appVersion",
        f"{values['postgresql']['image']['repository']}:{values['postgresql']['image']['tag']}": "the PostgreSQL image",
        f"{values['rustfs']['image']['repository']}:{values['rustfs']['image']['tag']}": "the RustFS image",
        f"{values['rustfs']['bucketInit']['image']['repository']}:{values['rustfs']['bucketInit']['image']['tag']}": "the AWS CLI image",
    }
    for image, what in expect.items():
        check(f"image: {image}\n" in images + "\n", f"the Artifact Hub image list names {what}", f"{image} not in {images!r}")
    check(chart["kubeVersion"].startswith(">="), "declares a minimum Kubernetes version")
    check(chart["icon"].startswith("https://"), "the icon is an https URL")
    check(not any("@" in str(m) for m in chart.get("maintainers", [])), "no personal e-mail address is published")
    check(values["mode"] == "standalone" and values["releaseChannel"] == "alpha", "defaults: standalone mode, alpha channel")
    check(values["postgresql"]["auth"]["password"] == "" and values["rustfs"]["auth"]["accessKey"] == "", "no default passwords are shipped")
    check(values["secrets"]["cipherKey"] == "", "no default cipher key is shipped")


def lint_all() -> None:
    section("lint · helm lint --strict over every shipped values file")
    files: list[Path | None] = [None]
    files += sorted((CHART / "ci").glob("*.yaml"))
    files += sorted((ROOT / "examples").glob("*.yaml"))
    for f in files:
        cmd = ["helm", "lint", str(CHART), "--strict"]
        if f:
            cmd += ["--values", str(f)]
        proc = subprocess.run(cmd, env=ENV, capture_output=True, text=True)
        label = f"{f.relative_to(ROOT)}" if f else "default values"
        check(proc.returncode == 0, f"{label}", proc.stdout[-400:] + proc.stderr[-400:])
        if f:
            out = render(extra=["--values", str(f)])
            check(out.returncode == 0, f"{label}: renders", out.stderr[:400])


def packaging() -> None:
    section("package · what a release contains")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(["helm", "package", str(CHART), "--destination", tmp], env=ENV, capture_output=True, text=True)
        check(proc.returncode == 0, "helm package", proc.stderr)
        archives = list(Path(tmp).glob("*.tgz"))
        check([a.name for a in archives] == ["molesignal-0.1.0.tgz"], "named after the chart version", str([a.name for a in archives]))
        if archives:
            with tarfile.open(archives[0]) as tar:
                names = tar.getnames()
            check("molesignal/Chart.yaml" in names and "molesignal/values.schema.json" in names, "contains the chart and its schema")
            check(not any(n.startswith("molesignal/ci/") for n in names), "leaves the CI values out")
            check(any(n.endswith("templates/rustfs.yaml") for n in names) and any(n.endswith("templates/postgresql.yaml") for n in names), "contains the bundled-service templates")
    _ = os


SCENARIOS = [guards, notes, metadata, lint_all, packaging]
