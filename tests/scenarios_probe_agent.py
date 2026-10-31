"""The probe-agent chart: what it creates, how an Agent gets and keeps its identity, the token, the
guards, and the same metadata and packaging checks as the molesignal chart."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from harness import (
    ENV,
    PROBE_CHART,
    ROOT,
    b64,
    chart_variant,
    check,
    container,
    env_of,
    integrity,
    pod_spec,
    render as render_chart,
    refuses as refuses_chart,
    section,
    unb64,
)

FULL = "rel-probe-agent"
ENDPOINT = "https://molesignal.example.com:5084"
CA = base64.b64encode(b"-----BEGIN CERTIFICATE-----\nnot a real one\n-----END CERTIFICATE-----\n").decode()
ONE_TIME_TOKEN = "qx3t-one-time-token-without-a-prefix-0123456789a"
AGENT_TOKEN = "ms_probe_an-agent-token-0123456789abcdefghijklmn"
MOUNT = "/var/lib/molesignal-probe"
STATE_DIR = f"{MOUNT}/state"


def values(**over: object) -> dict:
    """What has to be set for the chart to render, with an existing Secret, and `over` on top."""
    registration = {"endpoint": ENDPOINT, "caCertificateBase64": CA, "existingSecret": "registration"}
    registration.update(over.pop("registration", {}))  # type: ignore[arg-type]
    return {"registration": registration, **over}


def render(vals: dict | None = None, sets: list[str] | None = None, *, chart: Path | str = PROBE_CHART, **kwargs: object):
    return render_chart(vals, sets, chart=chart, **kwargs)  # type: ignore[arg-type]


def refuses(msg: str, expect: str, vals: dict | None = None, sets: list[str] | None = None) -> None:
    refuses_chart(msg, expect, vals, sets, chart=PROBE_CHART)


def install() -> None:
    section("probe-agent · what an install creates")
    m = render(values())
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "probe-agent")

    check(
        m.kinds()
        == sorted(
            [
                ("ConfigMap", f"{FULL}-chromium-flags"),
                ("Pod", f"{FULL}-test-connection"),
                ("Service", FULL),
                ("ServiceAccount", FULL),
                ("StatefulSet", FULL),
            ]
        ),
        "creates exactly the expected objects: with an existing Secret the chart makes none",
        str(m.kinds()),
    )

    agents = m.get("StatefulSet", FULL)
    main = container(agents, "probe-agent")
    register = container(agents, "register")
    spec = pod_spec(agents)
    check(main["image"] == "ghcr.io/molesignal/probe-agent:latest", "runs the Agent image at its latest tag", main["image"])
    check(main["imagePullPolicy"] == "Always", "and pulls it every time, since latest moves")
    check(register["image"] == main["image"] and register["imagePullPolicy"] == "Always", "the init container uses the same image")
    check("command" not in main, "keeps the image's entrypoint, so dumb-init stays PID 1")
    check(
        main["args"] == ["--state-dir", STATE_DIR, "run", "--max-concurrent", "4", "--max-browser-concurrent", "1"],
        "runs the Agent with its capacities",
        str(main["args"]),
    )
    check(agents["spec"]["replicas"] == 1, "one replica by default")
    check(agents["spec"]["podManagementPolicy"] == "Parallel", "a replica that cannot register does not hold back the next one")
    check(spec["automountServiceAccountToken"] is False, "does not mount the service account token")
    check("ports" not in main and "readinessProbe" not in main and "livenessProbe" not in main, "no ports and no probes: an Agent only connects out")
    check(main["resources"]["limits"]["memory"] == "2Gi" and main["resources"]["requests"]["memory"] == "512Mi", "has room for one Chromium, and a limit")

    service = m.get("Service", FULL)
    check(
        service["spec"]["clusterIP"] == "None" and "ports" not in service["spec"] and agents["spec"]["serviceName"] == FULL,
        "the Service the StatefulSet has to name is headless and has no ports",
    )

    selector = agents["spec"]["selector"]["matchLabels"]
    check(
        selector == {"app.kubernetes.io/name": "probe-agent", "app.kubernetes.io/instance": "rel", "app.kubernetes.io/component": "agent"},
        "the selector is the three stable labels",
        str(selector),
    )
    labels = render(values(commonLabels={"team": "obs"}))
    claim = labels.get("StatefulSet", FULL)["spec"]["volumeClaimTemplates"][0]
    check(
        labels.get("StatefulSet", FULL)["spec"]["selector"]["matchLabels"] == selector and claim["metadata"]["labels"] == selector,
        "commonLabels reach neither the selector nor the volume claims, which cannot change in place",
    )
    check(labels.get("StatefulSet", FULL)["metadata"]["labels"]["team"] == "obs", "they do reach the StatefulSet")

    secure = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True}
    for c in (main, register):
        context = c["securityContext"]
        check(all(context.get(k) == v for k, v in secure.items()), f"{c['name']}: runs as the image's user, on a read-only root")
        check(
            context["capabilities"] == {"drop": ["ALL"]} and context["seccompProfile"] == {"type": "RuntimeDefault"},
            f"{c['name']}: no capabilities, the runtime's seccomp profile (the restricted Pod Security Standard)",
        )
    check(spec["securityContext"]["fsGroup"] == 10001, "the volume is group-writable for the image's group")
    check(
        {"name": "net.ipv4.ping_group_range", "value": "0 2147483647"} in spec["securityContext"]["sysctls"],
        "the Agent may send pings without a capability",
    )


def identity() -> None:
    section("probe-agent · an Agent registers once and keeps its identity")
    m = render(values())
    agents = m.get("StatefulSet", FULL)
    main = container(agents, "probe-agent")
    register = container(agents, "register")

    claim = agents["spec"]["volumeClaimTemplates"][0]
    check(
        claim["metadata"]["name"] == "state" and claim["spec"]["resources"]["requests"]["storage"] == "1Gi" and claim["spec"]["accessModes"] == ["ReadWriteOnce"],
        "every replica has a claim of its own",
    )
    mounts = {c["name"]: {vm["name"]: vm["mountPath"] for vm in c["volumeMounts"]} for c in (main, register)}
    check(mounts["probe-agent"]["state"] == MOUNT and mounts["register"]["state"] == MOUNT, "both containers mount it")
    env = env_of(register)
    check(
        env["STATE_DIR"]["value"] == STATE_DIR and main["args"][1] == STATE_DIR,
        "both use the same state directory",
    )
    check(
        STATE_DIR.startswith(MOUNT + "/") and STATE_DIR != MOUNT,
        "a subdirectory of the volume: register does chmod 0700 on it, which the root of a volume refuses",
    )
    script = register["args"][0]
    check(register["command"] == ["/bin/sh", "-ec"], "registers from a script, which stops at the first error")
    check(
        script.index('[ -e "$STATE_DIR/identity.json" ]') < script.index("exec probe-agent"),
        "looks for an identity first: register refuses to replace one, which would hold the pod in Init for good",
    )
    check(re.search(r'identity\.json" \]; then\n.*\n\s+exit 0', script) is not None, "and with an identity, does nothing")
    check(
        all(f'{flag} "$' in script for flag in ("--state-dir", "--endpoint", "--token", "--ca-certificate-base64")),
        "passes the state directory, the endpoint, the token and the CA",
    )
    check("$REGISTER_TOKEN" not in script.replace('--token "$REGISTER_TOKEN"', "").replace('"${REGISTER_TOKEN:-}"', ""), "and never prints the token")
    check(env["REGISTER_ENDPOINT"]["value"] == ENDPOINT and env["CA_CERTIFICATE"]["value"] == CA, "from the values")

    off = render(values(persistence={"enabled": False}))
    agents_off = off.get("StatefulSet", FULL)
    check(
        "volumeClaimTemplates" not in agents_off["spec"] and {"name": "state", "emptyDir": {}} in pod_spec(agents_off)["volumes"],
        "persistence off uses an emptyDir",
    )
    integrity(off, "persistence off")

    claim_options = render(values(persistence={"size": "5Gi", "storageClass": "fast", "annotations": {"a": "b"}}, persistentVolumeClaimRetentionPolicy={"whenScaled": "Delete"}))
    spec = claim_options.get("StatefulSet", FULL)["spec"]
    check(
        spec["volumeClaimTemplates"][0]["spec"]["storageClassName"] == "fast"
        and spec["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "5Gi"
        and spec["volumeClaimTemplates"][0]["metadata"]["annotations"] == {"a": "b"}
        and spec["persistentVolumeClaimRetentionPolicy"] == {"whenScaled": "Delete"},
        "size, class, annotations and retention follow the values",
    )
    empty_class = render(values(persistence={"storageClass": "-"})).get("StatefulSet", FULL)["spec"]["volumeClaimTemplates"][0]["spec"]
    check(empty_class["storageClassName"] == "", "`-` asks for no storage class")


def token() -> None:
    section("probe-agent · the token")
    existing = render(values(registration={"existingSecret": "mine", "existingSecretKey": "agent-token"}))
    ref = env_of(container(existing.get("StatefulSet", FULL), "register"))["REGISTER_TOKEN"]["valueFrom"]["secretKeyRef"]
    check(ref == {"name": "mine", "key": "agent-token", "optional": True}, "an existing Secret is read, and may be gone once there is an identity", str(ref))
    check(not existing.of("Secret"), "the chart makes no Secret of its own for it")

    inline = render(values(registration={"existingSecret": "", "token": ONE_TIME_TOKEN}))
    check(inline.returncode == 0, "an inline token renders", inline.stderr)
    integrity(inline, "inline token")
    secret = inline.get("Secret", f"{FULL}-registration")
    check(unb64(secret["data"]["token"]) == ONE_TIME_TOKEN, "an inline token goes into a Secret of the chart")
    check("helm.sh/resource-policy" not in secret["metadata"].get("annotations", {}), "which does not outlive the release: an Agent only needs it to register")
    ref = env_of(container(inline.get("StatefulSet", FULL), "register"))["REGISTER_TOKEN"]["valueFrom"]["secretKeyRef"]
    check(ref == {"name": f"{FULL}-registration", "key": "token", "optional": True}, "and the init container reads it from there")
    check(
        ONE_TIME_TOKEN not in inline.stdout and b64(ONE_TIME_TOKEN) in inline.stdout and not any(ONE_TIME_TOKEN in json.dumps(d) for d in inline.docs if d["kind"] != "Secret"),
        "the token appears in no manifest but that Secret, and only encoded",
    )
    check("REGISTER_TOKEN" not in env_of(container(inline.get("StatefulSet", FULL), "probe-agent")), "the running Agent does not get it at all")

    other = render(values(registration={"existingSecret": "", "token": AGENT_TOKEN}))
    check(
        other.get("StatefulSet", FULL)["spec"]["template"] == inline.get("StatefulSet", FULL)["spec"]["template"],
        "a new token restarts nothing: Agents that have an identity no longer read it",
    )

    defaults = json.loads(subprocess.run(["yq", "-o=json", ".", str(PROBE_CHART / "values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(defaults["registration"]["token"] == "" and defaults["registration"]["existingSecret"] == "", "no default token is shipped")
    upgrade = render(values(registration={"existingSecret": ""}), extra=["--is-upgrade"])
    check(upgrade.returncode == 0 and not upgrade.of("Secret"), "an upgrade may leave the token out, when the Agents have their identities", upgrade.stderr)
    check(env_of(container(upgrade.get("StatefulSet", FULL), "register"))["REGISTER_TOKEN"]["valueFrom"]["secretKeyRef"]["optional"] is True, "and the pods still start")


def browser() -> None:
    section("probe-agent · Chromium")
    m = render(values())
    agents = m.get("StatefulSet", FULL)
    main = container(agents, "probe-agent")
    mounts = {vm["name"]: vm for vm in main["volumeMounts"]}
    volumes = {v["name"]: v for v in pod_spec(agents)["volumes"]}
    check(
        mounts["tmp"]["mountPath"] == "/tmp" and volumes["tmp"] == {"name": "tmp", "emptyDir": {}},
        "/tmp is writable on the read-only root, for the profile and the shared memory",
    )
    check(mounts["home"]["mountPath"] == "/home/molesignal" and "emptyDir" in volumes["home"], "and so is the home directory, for its caches")
    check(not any(vm["mountPath"] == "/dev/shm" for vm in main["volumeMounts"]), "/dev/shm stays as it is: the Agent starts Chromium with --disable-dev-shm-usage")

    flag = mounts["chromium-flags"]
    check(
        flag["mountPath"] == "/etc/chromium.d/no-sandbox" and flag["subPath"] == "no-sandbox" and flag["readOnly"] is True,
        "no sandbox by default, through a file the image's launcher sources: it clears CHROMIUM_FLAGS from the environment",
    )
    check(
        m.get("ConfigMap", f"{FULL}-chromium-flags")["data"]["no-sandbox"].strip() == 'export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox"',
        "the file adds --no-sandbox to the launcher's flags",
    )
    check(
        volumes["chromium-flags"]["configMap"]["name"] == f"{FULL}-chromium-flags",
        "and the volume names that ConfigMap",
    )

    sandboxed = render(values(browser={"sandbox": True}))
    integrity(sandboxed, "sandbox on")
    main_sandboxed = container(sandboxed.get("StatefulSet", FULL), "probe-agent")
    check(
        not sandboxed.of("ConfigMap") and not any(vm["name"] == "chromium-flags" for vm in main_sandboxed["volumeMounts"]),
        "`browser.sandbox` leaves the launcher alone",
    )

    no_browser = render(values(browser={"maxConcurrent": 0}, maxConcurrent=8))
    args = container(no_browser.get("StatefulSet", FULL), "probe-agent")["args"]
    check(args[-4:] == ["--max-concurrent", "8", "--max-browser-concurrent", "0"], "`browser.maxConcurrent: 0` reaches the Agent, which then offers no browser checks", str(args))

    memory = render(values(browser={"tmp": {"medium": "Memory", "sizeLimit": "1Gi"}}))
    check(
        {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "1Gi"}} in pod_spec(memory.get("StatefulSet", FULL))["volumes"],
        "`browser.tmp` configures the volume",
    )


def options() -> None:
    section("probe-agent · image, scheduling and names")
    mirrored = render(values(**{"global": {"imageRegistry": "registry.example.com/", "imagePullSecrets": [{"name": "global"}]}, "imagePullSecrets": [{"name": "own"}]}))
    agents = mirrored.get("StatefulSet", FULL)
    test = mirrored.get("Pod", f"{FULL}-test-connection")
    image = "registry.example.com/molesignal/probe-agent:latest"
    check(
        all(container(agents, n)["image"] == image for n in ("probe-agent", "register")) and container(test, "register-refused")["image"] == image,
        "global.imageRegistry replaces the registry host of every container, the test included",
        container(agents, "probe-agent")["image"],
    )
    for doc in (agents, test):
        check(pod_spec(doc)["imagePullSecrets"] == [{"name": "global"}, {"name": "own"}], f"{doc['kind']}: pull secrets merge")

    digest = "sha256:" + "a" * 64
    pinned = render(values(image={"tag": "26.2.0.0", "digest": digest, "pullPolicy": "IfNotPresent"}))
    main = container(pinned.get("StatefulSet", FULL), "probe-agent")
    check(main["image"] == f"ghcr.io/molesignal/probe-agent:26.2.0.0@{digest}" and main["imagePullPolicy"] == "IfNotPresent", "tag, digest and pull policy follow the values", main["image"])

    extras = values(
        replicaCount=3,
        nodeSelector={"site": "edge"},
        tolerations=[{"key": "edge", "operator": "Exists"}],
        affinity={"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": []}}},
        topologySpreadConstraints=[{"maxSkew": 1, "topologyKey": "zone", "whenUnsatisfiable": "ScheduleAnyway"}],
        priorityClassName="important",
        podAnnotations={"team": "obs"},
        podLabels={"tier": "probes"},
        logLevel="probe_agent=debug",
        extraEnv=[{"name": "HTTPS_PROXY", "value": "http://proxy:3128"}],
        extraEnvFrom=[{"secretRef": {"name": "more"}}],
        extraVolumes=[{"name": "ca", "configMap": {"name": "ca-bundle"}}],
        extraVolumeMounts=[{"name": "ca", "mountPath": "/etc/ca"}],
        extraObjects=[{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ca-bundle"}, "data": {"ca.crt": "x"}}],
        serviceAccount={"annotations": {"a": "b"}},
        commonAnnotations={"owner": "obs"},
    )
    m = render(extras)
    check(m.returncode == 0, "renders with everything set", m.stderr)
    integrity(m, "everything set")
    agents = m.get("StatefulSet", FULL)
    spec = pod_spec(agents)
    check(agents["spec"]["replicas"] == 3, "replicas")
    check(
        spec["nodeSelector"] == extras["nodeSelector"]
        and spec["tolerations"] == extras["tolerations"]
        and spec["affinity"] == extras["affinity"]
        and spec["topologySpreadConstraints"] == extras["topologySpreadConstraints"]
        and spec["priorityClassName"] == "important",
        "scheduling",
    )
    template = agents["spec"]["template"]["metadata"]
    check(template["annotations"] == {"team": "obs"} and template["labels"]["tier"] == "probes", "pod annotations and labels")
    for name in ("probe-agent", "register"):
        c = container(agents, name)
        check(
            env_of(c)["RUST_LOG"]["value"] == "probe_agent=debug" and env_of(c)["HTTPS_PROXY"]["value"] == "http://proxy:3128" and c["envFrom"] == extras["extraEnvFrom"],
            f"{name}: log filter, extra env and envFrom",
        )
        check({"name": "ca", "mountPath": "/etc/ca"} in c["volumeMounts"], f"{name}: extra mounts")
    check({"name": "ca", "configMap": {"name": "ca-bundle"}} in spec["volumes"] and m.has("ConfigMap", "ca-bundle"), "extra volumes and objects")
    check(m.get("ServiceAccount", FULL)["metadata"]["annotations"] == {"a": "b", "owner": "obs"}, "annotations merge")

    no_account = render(values(serviceAccount={"create": False, "name": "shared"}))
    check(not no_account.of("ServiceAccount") and pod_spec(no_account.get("StatefulSet", FULL))["serviceAccountName"] == "shared", "an existing service account")

    same = render(values(), release="probe-agent")
    check(same.has("StatefulSet", "probe-agent"), "a release named after the chart does not repeat the name")
    long = render(values(), release="a-release-name-that-goes-on-and-on-and-on-and-on-and")
    integrity(long, "a long release name")
    check(all(len(d["metadata"]["name"]) <= 63 for d in long.docs), "a long release name still gives valid names")


def connection_test() -> None:
    section("probe-agent · helm test")
    m = render(values())
    pod = m.get("Pod", f"{FULL}-test-connection")
    check(pod["metadata"]["annotations"]["helm.sh/hook"] == "test", "is a test hook")
    check(pod["spec"]["restartPolicy"] == "Never", "runs once")
    c = container(pod, "register-refused")
    env = env_of(c)
    check(env["ENDPOINT"]["value"] == ENDPOINT and env["CA_CERTIFICATE"]["value"] == CA, "tests the endpoint and the CA the Agents register with")
    script = c["args"][0]
    check(not any("secretKeyRef" in json.dumps(e) for e in c["env"]), "does not read the real token, which it would use up")
    check("--token not-a-real-token" in script, "uses a token that the server refuses before it stores anything")
    check('*"invalid Probe register token"*) echo' in script and 'exit 1' in script.split('*) echo "the server did not answer')[1], "passes only on that refusal, from the server itself")
    check("identity" not in script and "--state-dir /tmp/state" in script, "and uses a state directory of its own")
    check(c["securityContext"]["runAsUser"] == 10001 and c["securityContext"]["readOnlyRootFilesystem"] is True, "with the Agent's security context")
    check(pod["metadata"]["labels"]["app.kubernetes.io/component"] == "test", "and labels that the Service does not select")


def guards() -> None:
    section("probe-agent · what the chart refuses, with a message that says what to change")
    refuses("the defaults: there is no server to register with", "`registration.endpoint` is required", {})
    refuses("no endpoint", "`registration.endpoint` is required", values(registration={"endpoint": ""}))
    refuses("an endpoint that is not https", "must start with https://", values(registration={"endpoint": "http://molesignal:5084"}))
    refuses("no CA", "`registration.caCertificateBase64` is required", values(registration={"caCertificateBase64": ""}))
    refuses("a CA that is plain PEM", "not the base64 of a PEM certificate", values(registration={"caCertificateBase64": "-----BEGIN CERTIFICATE-----"}))
    refuses("a CA that is its sha256", "not the base64 of a PEM certificate", values(registration={"caCertificateBase64": "ab" * 32}))
    refuses("no token on install", "no registration token", values(registration={"existingSecret": ""}))
    refuses("two tokens", "not both", values(registration={"token": AGENT_TOKEN}))
    refuses("a one-time token for two replicas", "looks like a one-time token", values(replicaCount=2, registration={"existingSecret": "", "token": ONE_TIME_TOKEN}))
    many = render(values(replicaCount=2, registration={"existingSecret": "", "token": AGENT_TOKEN}))
    check(many.returncode == 0, "an Agent Token for two replicas is fine", many.stderr)
    many = render(values(replicaCount=2))
    check(many.returncode == 0, "an existing Secret for two replicas renders, since it cannot be read from here", many.stderr)
    refuses("more browser slots than slots", "`browser.maxConcurrent` is above `maxConcurrent`", values(maxConcurrent=1, browser={"maxConcurrent": 2}))
    refuses("a typo in a top-level key", "Additional property replicas is not allowed", values(replicas=2))
    refuses("a typo under registration", "Additional property endpont is not allowed", values(registration={"endpont": "x"}))
    refuses("a value of the wrong type", "Expected: integer", values(), ["replicaCount=many"])
    refuses("more slots than the Agent allows", "maxConcurrent", values(), ["maxConcurrent=33"])
    refuses("more browser slots than the Agent allows", "maxConcurrent", values(), ["browser.maxConcurrent=5"])
    refuses("a malformed digest", "digest", values(), ["image.digest=latest"])
    refuses("a storage size that is not a quantity", "size", values(), ["persistence.size=lots"])


def notes() -> None:
    section("probe-agent · the text helm install prints")
    with chart_variant(notes_preview=True, source=PROBE_CHART) as chart:

        def text(vals: dict) -> str:
            out = render(vals, chart=chart)
            assert out.returncode == 0, out.stderr
            return out.get("ConfigMap", "notes-preview")["data"]["notes"]

        base = text(values())
        check(ENDPOINT in base, "names the server")
        check("logs rel-probe-agent-0 --container register" in base, "tells you where to watch the registration")
        check("helm test rel --namespace ns" in base, "tells you how to test the connection")
        check("Synthetics > Agents" in base, "and where the Agents show up")
        check("state-rel-probe-agent-<ordinal>" in base and "revoke" in base, "and how to retire one")
        check("one-time token" not in base and "emptyDir" not in base.replace("an emptyDir", ""), "one replica on a volume needs no warning")
        check("1 replica" in base, "counts the replicas")
        check("one-time token" in text(values(replicaCount=2)), "warns about one-time tokens with more than one replica")
        check("every new pod registers as a new Agent" in text(values(persistence={"enabled": False})), "warns without persistence")


def metadata() -> None:
    section("probe-agent · Chart.yaml and values agree")
    chart = json.loads(subprocess.run(["yq", "-o=json", ".", str(PROBE_CHART / "Chart.yaml")], capture_output=True, text=True, check=True).stdout)
    defaults = json.loads(subprocess.run(["yq", "-o=json", ".", str(PROBE_CHART / "values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(chart["name"] == "probe-agent", "the chart is called probe-agent")
    check(re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", chart["version"]) is not None, f"version {chart['version']} is SemVer")
    check(chart["appVersion"] == "latest" and defaults["image"]["tag"] == "", "the Agent image follows latest, through appVersion")
    images = chart["annotations"]["artifacthub.io/images"]
    expected = f"{defaults['image']['repository']}:{chart['appVersion']}"
    check(f"image: {expected}\n" in images, "the Artifact Hub image list names the Agent image at appVersion", f"{expected} not in {images!r}")
    check(defaults["image"]["pullPolicy"] == "Always", "pulled every time, since latest moves")
    check(chart["kubeVersion"].startswith(">="), "declares a minimum Kubernetes version")
    check(chart["icon"].startswith("https://"), "the icon is an https URL")
    check(not any("@" in str(m) for m in chart.get("maintainers", [])), "no personal e-mail address is published")
    check(chart["sources"][0] == "https://github.com/molesignal/molesignal-helm", "the first source ties the OCI package to this repository")


def lint_all() -> None:
    section("probe-agent · helm lint --strict over every shipped values file")
    files = sorted((PROBE_CHART / "ci").glob("*-values.yaml")) + sorted((ROOT / "examples" / "probe-agent").glob("*.yaml"))
    check(len(files) >= 2, "there are files to lint", str(files))
    for f in files:
        proc = subprocess.run(["helm", "lint", str(PROBE_CHART), "--strict", "--values", str(f)], env=ENV, capture_output=True, text=True)
        check(proc.returncode == 0, f"{f.name}", proc.stdout[-400:] + proc.stderr[-400:])
        out = render(extra=["--values", str(f)])
        check(out.returncode == 0, f"{f.name}: renders", out.stderr[:400])
        if out.returncode == 0:
            integrity(out, f.name)


def packaging() -> None:
    section("probe-agent · what a release contains")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(["helm", "package", str(PROBE_CHART), "--destination", tmp], env=ENV, capture_output=True, text=True)
        check(proc.returncode == 0, "helm package", proc.stderr)
        archives = list(Path(tmp).glob("*.tgz"))
        version = subprocess.run(["yq", ".version", str(PROBE_CHART / "Chart.yaml")], capture_output=True, text=True, check=True).stdout.strip()
        check([a.name for a in archives] == [f"probe-agent-{version}.tgz"], "named after the chart version", str([a.name for a in archives]))
        if archives:
            with tarfile.open(archives[0]) as tar:
                names = tar.getnames()
            check("probe-agent/Chart.yaml" in names and "probe-agent/values.schema.json" in names and "probe-agent/README.md" in names, "contains the chart, its schema and its README")
            check(not any(n.startswith("probe-agent/ci/") for n in names), "leaves the CI values out")


SCENARIOS = [install, identity, token, browser, options, connection_test, guards, notes, metadata, lint_all, packaging]
