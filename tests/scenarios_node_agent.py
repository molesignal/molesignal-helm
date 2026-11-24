"""The node-agent chart: what it creates, that its DaemonSet is the agent's own manifest
(deploy/kubernetes/node-agent.yaml in molesignal-node-agent), the `MS_*` settings, the host mounts
that can be switched off, the token, the namespace and priority options, the guards, and the same
metadata and packaging checks as the other charts."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from harness import (
    ENV,
    NODE_CHART,
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

FULL = "molesignal-node-agent"
RELEASE = "molesignal"
ENDPOINT = "https://intake.molesignal.example"
TOKEN = "an-agent-bearer-token-0123456789abcdefghijklmnop"

# The agent's manifest (deploy/kubernetes/node-agent.yaml of molesignal-node-agent, 2026-11-24), which
# the chart mirrors. Kept here
# rather than read from that repository, which CI does not check out. A difference between the two
# has to be deliberate: change this together with the chart, and say why in the CHANGELOG.
MANIFEST_SECURITY_CONTEXT = {
    "privileged": False,
    "runAsUser": 0,
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "seccompProfile": {"type": "Unconfined"},
    "capabilities": {
        "drop": ["ALL"],
        "add": ["BPF", "PERFMON", "SYS_ADMIN", "SYS_RESOURCE", "SYS_PTRACE", "DAC_READ_SEARCH", "NET_ADMIN", "SYSLOG", "KILL"],
    },
}
MANIFEST_READINESS = {
    "exec": {"command": ["molesignal-node-agent", "health-check", "--endpoint", "readyz"]},
    "periodSeconds": 10,
    "failureThreshold": 3,
}
MANIFEST_LIVENESS = {
    "exec": {"command": ["molesignal-node-agent", "health-check", "--endpoint", "healthz"]},
    "initialDelaySeconds": 30,
    "periodSeconds": 20,
    "failureThreshold": 3,
}
MANIFEST_RESOURCES = {"requests": {"cpu": "50m", "memory": "96Mi"}, "limits": {"memory": "512Mi"}}
MANIFEST_MOUNTS = [
    {"name": "token", "mountPath": "/etc/molesignal-node-agent", "readOnly": True},
    {"name": "state", "mountPath": "/var/lib/molesignal-node-agent"},
    {"name": "machine-id", "mountPath": "/etc/machine-id", "readOnly": True},
    {"name": "os-release", "mountPath": "/etc/os-release", "readOnly": True},
    {"name": "btf", "mountPath": "/sys/kernel/btf", "readOnly": True},
    {"name": "tracefs", "mountPath": "/sys/kernel/tracing", "readOnly": True},
    {"name": "debugfs", "mountPath": "/sys/kernel/debug", "readOnly": True},
    {"name": "cgroup", "mountPath": "/sys/fs/cgroup", "readOnly": True},
    {"name": "kmsg", "mountPath": "/dev/kmsg", "readOnly": True},
    {"name": "docker-sock", "mountPath": "/var/run/docker.sock", "readOnly": True},
]
MANIFEST_HOST_VOLUMES = [
    {"name": "state", "hostPath": {"path": "/var/lib/molesignal-node-agent", "type": "DirectoryOrCreate"}},
    {"name": "machine-id", "hostPath": {"path": "/etc/machine-id", "type": "File"}},
    {"name": "os-release", "hostPath": {"path": "/etc/os-release", "type": "File"}},
    {"name": "btf", "hostPath": {"path": "/sys/kernel/btf", "type": ""}},
    {"name": "tracefs", "hostPath": {"path": "/sys/kernel/tracing", "type": ""}},
    {"name": "debugfs", "hostPath": {"path": "/sys/kernel/debug", "type": ""}},
    {"name": "cgroup", "hostPath": {"path": "/sys/fs/cgroup", "type": "Directory"}},
    {"name": "kmsg", "hostPath": {"path": "/dev/kmsg", "type": "CharDevice"}},
    {"name": "docker-sock", "hostPath": {"path": "/var/run/docker.sock", "type": ""}},
]
MANIFEST_ENV = [
    {"name": "MS_TOKEN_PATH", "value": "/etc/molesignal-node-agent/token"},
    {"name": "MS_STATE_DIR", "value": "/var/lib/molesignal-node-agent"},
]
MANIFEST_CONFIG = {"MS_ENDPOINT": ENDPOINT, "MS_LOG_FORMAT": "json", "MS_HEALTH_ADDR": "127.0.0.1:9102", "MS_DROPWATCH_FILTER": "tcp"}

# value key -> volume name, for the mounts that can be switched off
OPTIONAL_MOUNTS = {
    "osRelease": "os-release",
    "btf": "btf",
    "tracefs": "tracefs",
    "debugfs": "debugfs",
    "cgroup": "cgroup",
    "kmsg": "kmsg",
    "dockerSocket": "docker-sock",
}


def values(**over: object) -> dict:
    """What has to be set for the chart to render, with an existing Secret, and `over` on top."""
    token = {"existingSecret": "molesignal-node-agent-token"}
    token.update(over.pop("token", {}))  # type: ignore[arg-type]
    return {"endpoint": ENDPOINT, "token": token, **over}


def render(vals: dict | None = None, sets: list[str] | None = None, *, chart: Path | str = NODE_CHART, release: str = RELEASE, **kwargs: object):
    return render_chart(vals, sets, chart=chart, release=release, namespace="molesignal", **kwargs)  # type: ignore[arg-type]


def refuses(msg: str, expect: str, vals: dict | None = None, sets: list[str] | None = None) -> None:
    out = render(vals, sets)
    check(out.returncode != 0 and expect in out.stderr, msg, f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]!r}")


def daemonset(m) -> dict:
    return m.get("DaemonSet", FULL)


def agent(m) -> dict:
    return container(daemonset(m), "agent")


def config_of(m) -> dict:
    return m.get("ConfigMap", FULL)["data"]


def install() -> None:
    section("node-agent · what an install creates")
    m = render(values())
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "node-agent")
    check(
        m.kinds() == sorted([("ConfigMap", FULL), ("DaemonSet", FULL), ("ServiceAccount", FULL)]),
        "creates exactly a ConfigMap, a DaemonSet and a ServiceAccount: with an existing Secret the chart makes none",
        str(m.kinds()),
    )
    check(
        all(d["metadata"]["name"] == FULL for d in m.docs),
        "a release called molesignal gives every object the name of the agent's manifest",
    )
    main = agent(m)
    check(main["image"] == "molesignal/molesignal-node-agent:0.1.0", "runs the agent's image at the chart's appVersion", main["image"])
    check(main["imagePullPolicy"] == "IfNotPresent", "and pulls a fixed tag once")
    check(main["args"] == ["run"] and "command" not in main, "runs `run` through the image's entrypoint")
    check("ports" not in main, "opens no port: the health endpoint is on loopback, and the agent only sends")

    ds = daemonset(m)
    selector = ds["spec"]["selector"]["matchLabels"]
    check(
        selector == {"app.kubernetes.io/name": "node-agent", "app.kubernetes.io/instance": RELEASE, "app.kubernetes.io/component": "agent"},
        "the selector is the three stable labels",
        str(selector),
    )
    labels = render(values(commonLabels={"team": "obs"}))
    check(daemonset(labels)["spec"]["selector"]["matchLabels"] == selector, "commonLabels do not reach the selector, which cannot change in place")
    check(all(d["metadata"]["labels"]["team"] == "obs" for d in labels.docs), "they do reach every object")
    check(ds["metadata"]["labels"]["app.kubernetes.io/part-of"] == "molesignal", "labelled as part of MoleSignal")


def manifest() -> None:
    section("node-agent · the DaemonSet is deploy/kubernetes/node-agent.yaml")
    m = render(values())
    ds = daemonset(m)
    spec = pod_spec(ds)
    main = agent(m)
    check(spec["hostPID"] is True and spec["hostNetwork"] is True, "host PID and network namespaces")
    check(spec["dnsPolicy"] == "ClusterFirstWithHostNet", "cluster DNS with the node's network")
    check(spec["terminationGracePeriodSeconds"] == 30, "30 seconds to drain")
    check(spec["priorityClassName"] == "system-node-critical", "system-node-critical")
    check(spec["tolerations"] == [{"operator": "Exists"}], "tolerates every taint")
    check(spec["serviceAccountName"] == FULL and spec["automountServiceAccountToken"] is False, "its own service account, without a token")
    check(m.get("ServiceAccount", FULL)["automountServiceAccountToken"] is False, "and the account does not mount one either")
    check(ds["spec"]["updateStrategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1}}, "one node at a time")
    check(main["securityContext"] == MANIFEST_SECURITY_CONTEXT, "the same security context: not privileged, the nine capabilities, read-only root", json.dumps(main["securityContext"]))
    check("securityContext" not in spec, "no pod security context, so no fsGroup that would open up the token")
    check(main["readinessProbe"] == MANIFEST_READINESS, "the same readiness probe")
    check(main["livenessProbe"] == MANIFEST_LIVENESS, "the same liveness probe")
    check(main["resources"] == MANIFEST_RESOURCES, "the same resources")
    check(main["volumeMounts"] == MANIFEST_MOUNTS, "the same mounts, in the same order", json.dumps(main["volumeMounts"]))
    volumes = spec["volumes"]
    check([v for v in volumes if "hostPath" in v] == MANIFEST_HOST_VOLUMES, "the same host paths and types", json.dumps(volumes))
    token = next(v for v in volumes if v["name"] == "token")["secret"]
    check(token["secretName"] == "molesignal-node-agent-token" and token["defaultMode"] == 0o400, "the token from its Secret, owner-only (0400)")
    check(token["items"] == [{"key": "token", "path": "token"}], "always as the file `token`, whatever the key in the Secret (the manifest needs the key `token`)")
    check(main["env"] == MANIFEST_ENV, "MS_TOKEN_PATH and MS_STATE_DIR point at the mounts")
    check(main["envFrom"] == [{"configMapRef": {"name": FULL}}], "everything else from the ConfigMap")
    check(config_of(m) == MANIFEST_CONFIG, "which has the manifest's four settings, the endpoint from the values", json.dumps(config_of(m)))
    check(spec["nodeSelector"] == {"kubernetes.io/os": "linux"}, "deliberately different: Linux nodes only, since every taint is tolerated")
    check("checksum/config" in ds["spec"]["template"]["metadata"]["annotations"], "deliberately different: a checksum of the settings, so that a change rolls the pods")


def config() -> None:
    section("node-agent · the MS_* settings")
    full = render(
        values(
            config={
                "logLevel": "debug",
                "logFormat": "text",
                "healthAddr": "127.0.0.1:9200",
                "shutdownTimeoutMs": 15000,
                "collectors": {"allow": ["oom", "dropwatch"], "deny": ["netdev_hw"]},
                "dropwatchFilter": "tcp and port 443",
                "netdev": {"eventsDevices": ["eth[0-9]+", "bond4"], "hwDevices": [], "dcbDevices": None},
                "legacyMetrics": True,
                "extra": {"MS_SPOOL_QUOTA_BYTES": "2147483648"},
            }
        )
    )
    check(full.returncode == 0, "renders with every setting", full.stderr)
    data = config_of(full)
    expected = {
        "MS_ENDPOINT": ENDPOINT,
        "MS_LOG_LEVEL": "debug",
        "MS_LOG_FORMAT": "text",
        "MS_HEALTH_ADDR": "127.0.0.1:9200",
        "MS_SHUTDOWN_TIMEOUT_MS": "15000",
        "MS_COLLECTORS_ALLOW": "oom,dropwatch",
        "MS_COLLECTORS_DENY": "netdev_hw",
        "MS_DROPWATCH_FILTER": "tcp and port 443",
        "MS_NETDEV_EVENTS_DEVICES": "eth[0-9]+,bond4",
        "MS_NETDEV_HW_DEVICES": "",
        "MS_COMPATIBILITY_LEGACY_METRICS": "true",
        "MS_SPOOL_QUOTA_BYTES": "2147483648",
    }
    check(data == expected, "every value becomes its variable, lists comma-joined, numbers without a decimal point", json.dumps(data))
    check("MS_NETDEV_DCB_DEVICES" not in data, "a null device list is left out: the agent then watches every interface")
    check(data["MS_NETDEV_HW_DEVICES"] == "", "an empty one is kept as empty: the agent then watches none")

    every_drop = config_of(render(values(config={"dropwatchFilter": ""})))
    check(every_drop.get("MS_DROPWATCH_FILTER") == "", "an empty dropwatch filter is kept: it reports every drop")
    agent_default = config_of(render(values(config={"dropwatchFilter": None, "logFormat": None, "healthAddr": None})))
    check(agent_default == {"MS_ENDPOINT": ENDPOINT}, "null leaves a variable to the agent's default", json.dumps(agent_default))
    off = config_of(render(values(config={"legacyMetrics": False})))
    check(off["MS_COMPATIBILITY_LEGACY_METRICS"] == "false", "false is written, not dropped")

    inline = render(values(token={"existingSecret": "", "value": TOKEN}))
    check(not any(TOKEN in v or b64(TOKEN) in v for v in config_of(inline).values()), "the token is not in the ConfigMap")
    base = daemonset(render(values()))["spec"]["template"]
    changed = daemonset(render(values(config={"logLevel": "debug"})))["spec"]["template"]
    check(
        base["metadata"]["annotations"]["checksum/config"] != changed["metadata"]["annotations"]["checksum/config"],
        "a changed setting changes the pod template, so the pods roll",
    )
    endpoint = daemonset(render(values(endpoint="https://other.example")))["spec"]["template"]
    check(base["metadata"]["annotations"]["checksum/config"] != endpoint["metadata"]["annotations"]["checksum/config"], "so does a changed endpoint")


def host_mounts() -> None:
    section("node-agent · host mounts")
    for key, name in OPTIONAL_MOUNTS.items():
        m = render(values(hostMounts={key: {"enabled": False}}))
        main = agent(m)
        volumes = pod_spec(daemonset(m))["volumes"]
        check(
            m.returncode == 0
            and not any(vm["name"] == name for vm in main["volumeMounts"])
            and not any(v["name"] == name for v in volumes)
            and len(main["volumeMounts"]) == len(MANIFEST_MOUNTS) - 1,
            f"`hostMounts.{key}.enabled: false` removes the {name} mount and its volume, and nothing else",
            m.stderr,
        )
    none = render(values(hostMounts={key: {"enabled": False} for key in OPTIONAL_MOUNTS}))
    integrity(none, "every optional mount off")
    names = [vm["name"] for vm in agent(none)["volumeMounts"]]
    check(names == ["token", "state", "machine-id"], "with all of them off, the token, the state and the machine-id remain", str(names))

    moved = render(values(hostMounts={"dockerSocket": {"hostPath": "/run/docker.sock"}, "machineId": {"hostPath": "/var/lib/dbus/machine-id"}}, state={"hostPath": "/data/molesignal"}))
    volumes = {v["name"]: v for v in pod_spec(daemonset(moved))["volumes"]}
    mounts = {vm["name"]: vm["mountPath"] for vm in agent(moved)["volumeMounts"]}
    check(
        volumes["docker-sock"]["hostPath"]["path"] == "/run/docker.sock" and mounts["docker-sock"] == "/var/run/docker.sock",
        "a host path can move; the path in the container, which the agent looks for, does not",
    )
    check(volumes["machine-id"]["hostPath"] == {"path": "/var/lib/dbus/machine-id", "type": "File"}, "machine-id too")
    check(
        volumes["state"]["hostPath"] == {"path": "/data/molesignal", "type": "DirectoryOrCreate"}
        and env_of(agent(moved))["MS_STATE_DIR"]["value"] == "/var/lib/molesignal-node-agent",
        "and the state directory, while MS_STATE_DIR stays the path in the container",
    )
    refuses("machine-id cannot be switched off", "Additional property enabled is not allowed", values(hostMounts={"machineId": {"enabled": False}}))
    refuses("a host path is absolute", "hostPath", values(), ["hostMounts.kmsg.hostPath=dev/kmsg"])


def token() -> None:
    section("node-agent · the token")
    existing = render(values(token={"existingSecret": "mine", "existingSecretKey": "agent.token"}))
    secret = next(v for v in pod_spec(daemonset(existing))["volumes"] if v["name"] == "token")["secret"]
    check(secret["secretName"] == "mine" and secret["items"] == [{"key": "agent.token", "path": "token"}], "an existing Secret, with any key, becomes the file `token`")
    check(not existing.of("Secret"), "the chart makes no Secret of its own for it")

    inline = render(values(token={"existingSecret": "", "value": TOKEN}))
    check(inline.returncode == 0, "an inline token renders", inline.stderr)
    integrity(inline, "inline token")
    made = inline.get("Secret", f"{FULL}-token")
    check(unb64(made["data"]["token"]) == TOKEN, "an inline token goes into a Secret of the chart")
    check("helm.sh/resource-policy" not in made["metadata"].get("annotations", {}), "which goes with the release")
    secret = next(v for v in pod_spec(daemonset(inline))["volumes"] if v["name"] == "token")["secret"]
    check(secret["secretName"] == f"{FULL}-token" and secret["defaultMode"] == 0o400, "and is mounted from there, owner-only")
    check(
        TOKEN not in inline.stdout and not any(TOKEN in json.dumps(d) or b64(TOKEN) in json.dumps(d) for d in inline.docs if d["kind"] != "Secret"),
        "the token appears in no manifest but that Secret, and only encoded",
    )
    check(not any("TOKEN" in e["name"] and "value" in e and e["value"] != "/etc/molesignal-node-agent/token" for e in agent(inline)["env"]), "and never as an environment variable")
    other = render(values(token={"existingSecret": "", "value": TOKEN + "x"}))
    check(daemonset(other)["spec"]["template"] == daemonset(inline)["spec"]["template"], "a new token restarts nothing: the agent re-reads the file")

    defaults = json.loads(subprocess.run(["yq", "-o=json", ".", str(NODE_CHART / "values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(defaults["token"]["value"] == "" and defaults["token"]["existingSecret"] == "", "no default token is shipped")
    upgrade = render(values(token={"existingSecret": ""}), extra=["--is-upgrade"])
    check(upgrade.returncode != 0 and "no token" in upgrade.stderr, "an upgrade needs a token too: the agent reads it for as long as it runs", upgrade.stderr[:200])


def namespace_and_priority() -> None:
    section("node-agent · namespace, Pod Security and priority")
    m = render(values())
    check(not m.of("Namespace") and not m.of("PriorityClass"), "by default neither a namespace nor a PriorityClass")

    ns = render(values(namespace={"create": True}))
    integrity(ns, "namespace.create")
    namespace = ns.get("Namespace", "molesignal")
    check(namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "privileged", "namespace.create makes the release's namespace with the privileged Pod Security level")
    check(namespace["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep", "and keeps it on uninstall, with whatever else is in it")
    check("namespace" not in namespace["metadata"], "a namespace has no namespace")
    custom = render(values(namespace={"create": True, "labels": {"pod-security.kubernetes.io/enforce": "privileged", "pod-security.kubernetes.io/warn": "privileged"}, "annotations": {"a": "b"}}))
    meta = custom.get("Namespace", "molesignal")["metadata"]
    check(meta["labels"]["pod-security.kubernetes.io/warn"] == "privileged" and meta["annotations"]["a"] == "b", "with labels and annotations from the values")

    pc = render(values(priorityClass={"create": True, "value": 5000, "preemptionPolicy": "Never"}))
    integrity(pc, "priorityClass.create")
    klass = pc.get("PriorityClass", FULL)
    check(klass["value"] == 5000 and klass["preemptionPolicy"] == "Never" and klass["globalDefault"] is False, "priorityClass.create makes a PriorityClass from the values")
    check(pod_spec(daemonset(pc))["priorityClassName"] == FULL, "and the pods use it instead of priorityClassName")
    none = render(values(priorityClassName=""))
    check("priorityClassName" not in pod_spec(daemonset(none)), "an empty priorityClassName leaves the field out")
    refuses("a PriorityClass above the user range", "value", values(priorityClass={"create": True, "value": 2000000000}))


def options() -> None:
    section("node-agent · image, scheduling and the rest")
    mirrored = render(values(**{"global": {"imageRegistry": "registry.example.com/", "imagePullSecrets": [{"name": "global"}]}, "imagePullSecrets": [{"name": "own"}]}))
    check(agent(mirrored)["image"] == "registry.example.com/molesignal/molesignal-node-agent:0.1.0", "global.imageRegistry goes in front of an image without a host", agent(mirrored)["image"])
    check(pod_spec(daemonset(mirrored))["imagePullSecrets"] == [{"name": "global"}, {"name": "own"}], "pull secrets merge")
    hosted = render(values(**{"global": {"imageRegistry": "mirror.example.com"}, "image": {"repository": "ghcr.io/molesignal/molesignal-node-agent"}}))
    check(agent(hosted)["image"] == "mirror.example.com/molesignal/molesignal-node-agent:0.1.0", "and replaces the host of one that has it", agent(hosted)["image"])

    digest = "sha256:" + "a" * 64
    pinned = render(values(image={"tag": "0.1.1", "digest": digest, "pullPolicy": "Always"}))
    check(agent(pinned)["image"] == f"molesignal/molesignal-node-agent:0.1.1@{digest}" and agent(pinned)["imagePullPolicy"] == "Always", "tag, digest and pull policy follow the values")

    extras = values(
        nodeSelector={"role": "worker"},
        tolerations=[{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}],
        affinity={"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": []}}},
        updateStrategy={"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": "10%"}},
        terminationGracePeriodSeconds=60,
        podAnnotations={"team": "obs"},
        podLabels={"tier": "nodes"},
        resources={"requests": {"cpu": "100m"}},
        securityContext={**MANIFEST_SECURITY_CONTEXT, "privileged": True, "seLinuxOptions": {"type": "spc_t"}},
        extraEnv=[{"name": "HTTPS_PROXY", "value": "http://proxy:3128"}],
        extraEnvFrom=[{"secretRef": {"name": "more"}}],
        extraVolumes=[{"name": "ca", "configMap": {"name": "ca-bundle"}}],
        extraVolumeMounts=[{"name": "ca", "mountPath": "/etc/ssl/extra"}],
        extraObjects=[{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ca-bundle"}, "data": {"ca.crt": "x"}}],
        serviceAccount={"annotations": {"a": "b"}},
        commonAnnotations={"owner": "obs"},
    )
    m = render(extras)
    check(m.returncode == 0, "renders with everything set", m.stderr)
    integrity(m, "everything set")
    ds = daemonset(m)
    spec = pod_spec(ds)
    main = agent(m)
    check(
        spec["nodeSelector"] == {"kubernetes.io/os": "linux", "role": "worker"} and spec["tolerations"] == extras["tolerations"] and spec["affinity"] == extras["affinity"],
        "scheduling: a node selector adds to the Linux one, tolerations replace every-taint",
    )
    check(ds["spec"]["updateStrategy"]["rollingUpdate"]["maxUnavailable"] == "10%" and spec["terminationGracePeriodSeconds"] == 60, "update strategy and grace period")
    template = ds["spec"]["template"]["metadata"]
    check(template["annotations"]["team"] == "obs" and "checksum/config" in template["annotations"] and template["labels"]["tier"] == "nodes", "pod annotations and labels, next to the checksum")
    check(main["securityContext"]["privileged"] is True and main["securityContext"]["seLinuxOptions"] == {"type": "spc_t"}, "the security context can be replaced, for SysRq stacks or SELinux")
    check(main["resources"] == {"requests": {"cpu": "100m", "memory": "96Mi"}, "limits": {"memory": "512Mi"}}, "resources merge with the defaults")
    env = env_of(main)
    check(env["HTTPS_PROXY"]["value"] == "http://proxy:3128" and "MS_TOKEN_PATH" in env, "extra env, next to the chart's")
    check(main["envFrom"] == [{"configMapRef": {"name": FULL}}, {"secretRef": {"name": "more"}}], "extra envFrom after the ConfigMap")
    check({"name": "ca", "mountPath": "/etc/ssl/extra"} in main["volumeMounts"] and {"name": "ca", "configMap": {"name": "ca-bundle"}} in spec["volumes"], "extra volumes and mounts")
    check(m.has("ConfigMap", "ca-bundle"), "extra objects")
    check(m.get("ServiceAccount", FULL)["metadata"]["annotations"] == {"a": "b", "owner": "obs"}, "annotations merge")

    no_account = render(values(serviceAccount={"create": False, "name": "shared"}))
    check(not no_account.of("ServiceAccount") and pod_spec(daemonset(no_account))["serviceAccountName"] == "shared", "an existing service account")
    other = render(values(), release="obs")
    check(other.has("DaemonSet", "obs-node-agent"), "another release name is followed by the chart's")
    long = render(values(token={"existingSecret": "", "value": TOKEN}), release="a-release-name-that-goes-on-and-on-and-on-and-on-and")
    integrity(long, "a long release name")
    check(all(len(d["metadata"]["name"]) <= 63 for d in long.docs), "a long release name still gives valid names, the token Secret's included")


def guards() -> None:
    section("node-agent · what the chart refuses, with a message that says what to change")
    refuses("the defaults: there is nowhere to send", "`endpoint` is required", {})
    refuses("no endpoint", "`endpoint` is required", values(endpoint=""))
    refuses("an endpoint without a scheme", "must start with http:// or https://", values(endpoint="intake.molesignal.example:4318"))
    plain = render(values(endpoint="http://molesignal-intake.molesignal.svc:4318"))
    check(plain.returncode == 0, "plain http to an intake in the cluster is allowed", plain.stderr)
    refuses("no token", "no token", values(token={"existingSecret": ""}))
    refuses("two tokens", "not both", values(token={"value": TOKEN}))
    refuses("managed enrollment, which does not exist yet", "`enrollment.managed` is reserved", values(enrollment={"managed": True}))
    refuses("fsGroup, which would open the token to a group", "podSecurityContext.fsGroup", values(podSecurityContext={"fsGroup": 1000}))
    other = render(values(podSecurityContext={"supplementalGroups": [1000]}))
    check(other.returncode == 0, "the rest of the pod security context is allowed", other.stderr)
    refuses("a collector both allowed and denied", "in both", values(config={"collectors": {"allow": ["oom"], "deny": ["oom"]}}))
    refuses("a device pattern with a comma", "cannot contain a comma", values(config={"netdev": {"hwDevices": ["eth0,eth1"]}}))
    refuses("a grace period shorter than the agent's default drain", "has to be longer", values(terminationGracePeriodSeconds=10))
    refuses("a drain longer than the grace period", "has to be longer", values(config={"shutdownTimeoutMs": 45000}))
    longer = render(values(config={"shutdownTimeoutMs": 45000}, terminationGracePeriodSeconds=60))
    check(longer.returncode == 0, "both longer is fine", longer.stderr)
    refuses("config.extra setting what the chart sets", "which the chart sets itself", values(config={"extra": {"MS_ENDPOINT": "https://elsewhere"}}))
    refuses("config.extra moving the token", "which the chart sets itself", values(config={"extra": {"MS_TOKEN_PATH": "/tmp/token"}}))
    refuses("config.extra with something that is not MS_*", "extra", values(config={"extra": {"HTTP_PROXY": "x"}}))
    refuses("config.extra with a number", "extra", values(config={"extra": {"MS_BATCH_MAX_RECORDS": 500}}))
    refuses("a health address off loopback", "healthAddr", values(config={"healthAddr": "0.0.0.0:9102"}))
    refuses("a log level the agent does not know", "logLevel", values(config={"logLevel": "verbose"}))
    refuses("a log format the agent does not know", "logFormat", values(config={"logFormat": "logfmt"}))
    refuses("a collector name the agent cannot have", "allow", values(config={"collectors": {"allow": ["Oom"]}}))
    refuses("a typo in a top-level key", "Additional property endpont is not allowed", values(endpont="x"))
    refuses("a typo under config", "Additional property loglevel is not allowed", values(config={"loglevel": "info"}))
    refuses("a typo in a host mount", "Additional property dockerSock is not allowed", values(hostMounts={"dockerSock": {"enabled": False}}))
    refuses("a malformed digest", "digest", values(), ["image.digest=latest"])


def notes() -> None:
    section("node-agent · the text helm install prints")
    with chart_variant(notes_preview=True, source=NODE_CHART) as chart:

        def text(vals: dict) -> str:
            out = render(vals, chart=chart)
            assert out.returncode == 0, out.stderr
            return out.get("ConfigMap", "notes-preview")["data"]["notes"]

        base = text(values())
        check(ENDPOINT in base, "names the endpoint")
        check("kubectl label namespace molesignal pod-security.kubernetes.io/enforce=privileged" in base, "tells you how to label the namespace for Pod Security")
        check("rollout status daemonset/molesignal-node-agent" in base, "tells you how to watch the rollout")
        check("exec daemonset/molesignal-node-agent -- molesignal-node-agent doctor" in base, "and how to ask an agent what works on its node")
        check("Switched off" not in base, "says nothing about mounts when all are on")
        created = text(values(namespace={"create": True}))
        check("kubectl label namespace" not in created, "a namespace the chart created needs no label")
        off = text(values(hostMounts={"dockerSocket": {"enabled": False}, "debugfs": {"enabled": False}}))
        check("Switched off here: debugfs, dockerSocket." in off, "names the mounts that are switched off")


def metadata() -> None:
    section("node-agent · Chart.yaml and values agree")
    chart = json.loads(subprocess.run(["yq", "-o=json", ".", str(NODE_CHART / "Chart.yaml")], capture_output=True, text=True, check=True).stdout)
    defaults = json.loads(subprocess.run(["yq", "-o=json", ".", str(NODE_CHART / "values.yaml")], capture_output=True, text=True, check=True).stdout)
    check(chart["name"] == "node-agent", "the chart is called node-agent")
    check(re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", chart["version"]) is not None, f"version {chart['version']} is SemVer")
    check(re.fullmatch(r"\d+\.\d+\.\d+", chart["appVersion"]) is not None and defaults["image"]["tag"] == "", "the image tag is the chart's appVersion, a fixed release")
    images = chart["annotations"]["artifacthub.io/images"]
    expected = f"{defaults['image']['repository']}:{chart['appVersion']}"
    check(f"image: {expected}\n" in images, "the Artifact Hub image list names the agent image at appVersion", f"{expected} not in {images!r}")
    check("linux/amd64" in images and "linux/arm64" in images, "for x86_64 and aarch64")
    check(defaults["image"]["pullPolicy"] == "IfNotPresent", "pulled once, since the tag is fixed")
    check(chart["annotations"]["artifacthub.io/license"] == "Apache-2.0", "Apache-2.0, like the agent")
    check(chart["kubeVersion"].startswith(">="), "declares a minimum Kubernetes version")
    check(not any("@" in str(m) for m in chart.get("maintainers", [])), "no personal e-mail address is published")
    check(chart["sources"][0] == "https://github.com/molesignal/molesignal-helm", "the first source ties the OCI package to this repository")
    check(defaults["enrollment"]["managed"] is False, "managed enrollment is off by default")


def lint_all() -> None:
    section("node-agent · helm lint --strict over every shipped values file")
    files = sorted((NODE_CHART / "ci").glob("*-values.yaml")) + sorted((ROOT / "examples" / "node-agent").glob("*.yaml"))
    check(len(files) >= 2, "there are files to lint", str(files))
    for f in files:
        proc = subprocess.run(["helm", "lint", str(NODE_CHART), "--strict", "--values", str(f)], env=ENV, capture_output=True, text=True)
        check(proc.returncode == 0, f"{f.name}", proc.stdout[-400:] + proc.stderr[-400:])
        out = render(extra=["--values", str(f)])
        check(out.returncode == 0, f"{f.name}: renders", out.stderr[:400])
        if out.returncode == 0:
            integrity(out, f.name)


def packaging() -> None:
    section("node-agent · what a release contains")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(["helm", "package", str(NODE_CHART), "--destination", tmp], env=ENV, capture_output=True, text=True)
        check(proc.returncode == 0, "helm package", proc.stderr)
        archives = list(Path(tmp).glob("*.tgz"))
        version = subprocess.run(["yq", ".version", str(NODE_CHART / "Chart.yaml")], capture_output=True, text=True, check=True).stdout.strip()
        check([a.name for a in archives] == [f"node-agent-{version}.tgz"], "named after the chart version", str([a.name for a in archives]))
        if archives:
            with tarfile.open(archives[0]) as tar:
                names = tar.getnames()
            check("node-agent/Chart.yaml" in names and "node-agent/values.schema.json" in names and "node-agent/README.md" in names, "contains the chart, its schema and its README")
            check(not any(n.startswith("node-agent/ci/") for n in names), "leaves the CI values out")


SCENARIOS = [install, manifest, config, host_mounts, token, namespace_and_priority, options, guards, notes, metadata, lint_all, packaging]
