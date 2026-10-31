"""The optional parts: scheduling, probes, autoscaling, budgets, ingress, naming."""

from __future__ import annotations

from harness import (
    check,
    container,
    containers,
    env_of,
    integrity,
    pod_spec,
    render,
    section,
)

FULL = "rel-molesignal"
SPEC_KEYS = ("nodeSelector", "tolerations", "affinity", "topologySpreadConstraints", "priorityClassName")




def scheduling() -> None:
    section("options · scheduling, security context and extra pod settings")
    values = {
        "mode": "distributed",
        "nodeSelector": {"disk": "ssd"},
        "tolerations": [{"key": "dedicated", "operator": "Exists"}],
        "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": []}}},
        "topologySpreadConstraints": [{"maxSkew": 1, "topologyKey": "zone", "whenUnsatisfiable": "ScheduleAnyway"}],
        "priorityClassName": "important",
        "podSecurityContext": {"fsGroup": 2000},
        "securityContext": {"allowPrivilegeEscalation": False},
        "podAnnotations": {"team": "obs"},
        "podLabels": {"tier": "telemetry"},
        "extraEnv": [{"name": "MS_COMPACTOR_INTERVAL_SECS", "value": "600"}],
        "extraEnvFrom": [{"secretRef": {"name": "more"}}],
        "extraVolumes": [{"name": "ca", "configMap": {"name": "ca-bundle"}}],
        "extraObjects": [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ca-bundle"}, "data": {"ca.crt": "x"}}],
        "extraVolumeMounts": [{"name": "ca", "mountPath": "/etc/ca"}],
        "querier": {"nodeSelector": {"disk": "nvme"}, "extraEnv": [{"name": "ONLY_QUERIER", "value": "1"}], "podAnnotations": {"team": "query"}},
        "intake": {"resources": {"requests": {"memory": "2Gi"}}},
    }
    m = render(values)
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "scheduling")

    for kind, role in (("Deployment", "router"), ("StatefulSet", "intake"), ("Deployment", "alert")):
        doc = m.get(kind, f"{FULL}-{role}")
        spec = pod_spec(doc)
        check(spec["nodeSelector"] == {"disk": "ssd"}, f"{role}: global nodeSelector")
        check(spec["tolerations"] == values["tolerations"] and spec["priorityClassName"] == "important", f"{role}: tolerations and priority")
        check(spec["topologySpreadConstraints"] == values["topologySpreadConstraints"], f"{role}: topology spread")
        check(spec["securityContext"] == {"fsGroup": 2000}, f"{role}: pod security context")
        main = container(doc, "molesignal")
        check(main["securityContext"] == {"allowPrivilegeEscalation": False}, f"{role}: container security context")
        check(
            env_of(main)["MS_COMPACTOR_INTERVAL_SECS"]["value"] == "600" and main["envFrom"] == values["extraEnvFrom"],
            f"{role}: extra env and envFrom",
        )
        check(
            {"name": "ca", "mountPath": "/etc/ca"} in main["volumeMounts"] and {"name": "ca", "configMap": {"name": "ca-bundle"}} in spec["volumes"],
            f"{role}: extra volume and mount",
        )
        labels = doc["spec"]["template"]["metadata"]["labels"]
        check(labels["tier"] == "telemetry", f"{role}: extra pod label")
    check(
        m.get("Deployment", f"{FULL}-router")["spec"]["template"]["metadata"]["annotations"]["team"] == "obs",
        "global pod annotation reaches the pods",
    )

    q = m.get("Deployment", f"{FULL}-querier")
    check(pod_spec(q)["nodeSelector"] == {"disk": "nvme"}, "querier: its own nodeSelector replaces the global one")
    check(pod_spec(q)["tolerations"] == values["tolerations"], "querier: still gets the global tolerations")
    check("ONLY_QUERIER" in env_of(container(q, "molesignal")), "querier: role extraEnv is added")
    check(q["spec"]["template"]["metadata"]["annotations"]["team"] == "query", "querier: role pod annotation wins")
    check(
        "ONLY_QUERIER" not in env_of(container(m.get("Deployment", f"{FULL}-router"), "molesignal")),
        "router: does not get the querier's env",
    )
    intake = container(m.get("StatefulSet", f"{FULL}-intake"), "molesignal")
    check(intake["resources"] == {"requests": {"memory": "2Gi"}}, "intake: resources")

    bundled = render({**values, "mode": "standalone"})
    for kind, name in (("StatefulSet", f"{FULL}-postgresql"), ("StatefulSet", f"{FULL}-rustfs")):
        spec = pod_spec(bundled.get(kind, name))
        check("nodeSelector" not in spec and "tolerations" not in spec, f"{name}: the bundled services ignore the global scheduling values")


def probes() -> None:
    section("options · probes")
    m = render(
        {
            "mode": "distributed",
            "router": {"readinessProbe": {"periodSeconds": 3, "initialDelaySeconds": 1}, "livenessProbe": {"httpGet": {"path": "/api/v1/healthz", "port": "http"}}},
            "querier": {"readinessProbe": {"tcpSocket": {"port": "http"}}},
            "intake": {"startupProbe": {"httpGet": {"path": "/api/v1/readyz", "port": "http"}, "failureThreshold": 60}},
        }
    )
    check(m.returncode == 0, "renders", m.stderr)
    r = container(m.get("Deployment", f"{FULL}-router"), "molesignal")
    check(
        r["readinessProbe"]["periodSeconds"] == 3 and r["readinessProbe"]["httpGet"]["path"] == "/api/v1/healthz",
        "a partial override keeps the rest of the built-in probe",
    )
    check(r["livenessProbe"]["httpGet"]["port"] == "http", "a liveness probe can be added")
    q = container(m.get("Deployment", f"{FULL}-querier"), "molesignal")
    check(q["readinessProbe"] == {"tcpSocket": {"port": "http"}}, "naming another handler replaces the probe instead of mixing handlers")
    i = container(m.get("StatefulSet", f"{FULL}-intake"), "molesignal")
    check(i["startupProbe"]["failureThreshold"] == 60, "a startup probe can be added")


def autoscaling_and_budgets() -> None:
    section("options · autoscaling and disruption budgets")
    m = render(
        {
            "mode": "distributed",
            "router": {
                "autoscaling": {"enabled": True, "minReplicas": 3, "maxReplicas": 12, "targetCPUUtilizationPercentage": 65, "targetMemoryUtilizationPercentage": 80, "behavior": {"scaleDown": {"stabilizationWindowSeconds": 300}}},
                "podDisruptionBudget": {"enabled": True, "minAvailable": 1, "maxUnavailable": None},
            },
            "querier": {"autoscaling": {"enabled": True}},
            "intake": {"podDisruptionBudget": {"enabled": True, "maxUnavailable": 1}},
            "compactor": {"podDisruptionBudget": {"enabled": True, "minAvailable": "50%", "maxUnavailable": None}},
        }
    )
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "autoscaling and budgets")
    check(m.names("HorizontalPodAutoscaler") == [f"{FULL}-querier", f"{FULL}-router"], "an HPA for router and querier")
    router = m.get("HorizontalPodAutoscaler", f"{FULL}-router")["spec"]
    check(router["minReplicas"] == 3 and router["maxReplicas"] == 12, "router HPA bounds")
    check(
        [(x["resource"]["name"], x["resource"]["target"]["averageUtilization"]) for x in router["metrics"]] == [("cpu", 65), ("memory", 80)],
        "router HPA targets CPU and memory",
    )
    check(router["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 300, "router HPA behavior is passed through")
    querier = m.get("HorizontalPodAutoscaler", f"{FULL}-querier")["spec"]
    check([x["resource"]["name"] for x in querier["metrics"]] == ["cpu"], "querier HPA defaults to CPU only")
    check("replicas" not in m.get("Deployment", f"{FULL}-router")["spec"], "the Deployment leaves the replica count to the HPA")

    pdbs = {d["metadata"]["name"]: d["spec"] for d in m.of("PodDisruptionBudget")}
    check(sorted(pdbs) == [f"{FULL}-compactor", f"{FULL}-intake", f"{FULL}-router"], "budgets only where asked")
    check(pdbs[f"{FULL}-router"]["minAvailable"] == 1 and "maxUnavailable" not in pdbs[f"{FULL}-router"], "minAvailable")
    check(pdbs[f"{FULL}-intake"]["maxUnavailable"] == 1, "maxUnavailable")
    check(pdbs[f"{FULL}-compactor"]["minAvailable"] == "50%", "percentages are kept as strings")

    off = render({"mode": "distributed"})
    check(not off.of("HorizontalPodAutoscaler") and not off.of("PodDisruptionBudget"), "off by default")
    check(not render({"router": {"autoscaling": {"enabled": True}}}).of("HorizontalPodAutoscaler"), "no HPA in standalone mode")


def ingress() -> None:
    section("options · ingress")
    hosts = [{"host": "obs.example.com", "paths": [{"path": "/", "pathType": "Prefix"}, {"path": "/legacy"}]}]
    tls = [{"secretName": "obs-tls", "hosts": ["obs.example.com"]}]
    annotations = {"nginx.ingress.kubernetes.io/proxy-buffering": "off"}
    for mode, service in (("standalone", FULL), ("distributed", f"{FULL}-router")):
        m = render(
            {
                "mode": mode,
                "ingress": {"enabled": True, "className": "nginx", "annotations": annotations, "hosts": hosts, "tls": tls},
                "otlpIngress": {"enabled": True, "className": "nginx", "annotations": {"nginx.ingress.kubernetes.io/backend-protocol": "GRPC"}, "hosts": [{"host": "otlp.example.com", "paths": [{"path": "/"}]}]},
            }
        )
        check(m.returncode == 0, f"{mode}: renders", m.stderr)
        integrity(m, f"{mode} ingress")
        ing = m.get("Ingress", FULL)["spec"]
        check(ing["ingressClassName"] == "nginx" and ing["tls"] == tls, f"{mode}: class and TLS")
        backends = [(p["path"], p["pathType"], p["backend"]["service"]["name"], p["backend"]["service"]["port"]) for p in ing["rules"][0]["http"]["paths"]]
        check(
            backends == [("/", "Prefix", service, {"name": "http"}), ("/legacy", "Prefix", service, {"name": "http"})],
            f"{mode}: UI traffic goes to {service}, pathType defaults to Prefix",
            str(backends),
        )
        check(m.get("Ingress", FULL)["metadata"]["annotations"] == annotations, f"{mode}: annotations")
        otlp = m.get("Ingress", f"{FULL}-otlp")["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
        check(otlp == {"name": service, "port": {"name": "otlp-grpc"}}, f"{mode}: OTLP goes to the gRPC port of {service}")
    check(not render().of("Ingress"), "no ingress by default")


def web_layer() -> None:
    section("options · the optional nginx layer")
    m = render({"mode": "distributed", "web": {"enabled": True, "image": {"repository": "registry.example.com/molesignal-web", "tag": "v1"}}, "ingress": {"enabled": True}})
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "web layer")
    web = container(m.get("Deployment", f"{FULL}-web"), "web")
    check(web["image"] == "registry.example.com/molesignal-web:v1", "uses the image you give it")
    check(env_of(web)["MS_BACKEND"]["value"] == f"{FULL}-router:5080", "proxies to the router")
    check(web["readinessProbe"]["httpGet"]["path"] == "/healthz" and web["resources"]["limits"]["memory"] == "256Mi", "probes and resources as upstream")
    path = m.get("Ingress", FULL)["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    check(path == {"name": f"{FULL}-web", "port": {"number": 80}}, "the ingress points at the web layer")
    check(m.get("Service", f"{FULL}-web")["spec"]["ports"][0]["port"] == 80, "web Service on port 80")
    tagless = render({"mode": "distributed", "web": {"enabled": True, "image": {"repository": "r/web"}}})
    check(
        container(tagless.get("Deployment", f"{FULL}-web"), "web")["image"] == "r/web:latest",
        "no tag means the application version",
    )


def naming() -> None:
    section("options · names")
    m = render(release="molesignal")
    check(m.has("StatefulSet", "molesignal") and m.has("Service", "molesignal-postgresql"), "a release named like the chart is not doubled")
    m = render(release="a-very-long-release-name-that-goes-on-and-on-and-on", sets=["mode=distributed"])
    integrity(m, "long release name")
    check(max(len(d["metadata"]["name"]) for d in m.docs) <= 63, "every name fits in 63 characters")
    m = render(sets=["fullnameOverride=obs", "nameOverride=ignored"])
    check(m.has("StatefulSet", "obs") and m.has("Service", "obs-rustfs"), "fullnameOverride is the prefix")
    labels = m.get("StatefulSet", "obs")["metadata"]["labels"]
    check(labels["app.kubernetes.io/name"] == "ignored", "nameOverride changes the name label")
    m = render(sets=["commonLabels.env=prod", "commonAnnotations.owner=sre"])
    app = m.get("StatefulSet", FULL)
    check(
        app["metadata"]["labels"]["env"] == "prod" and app["metadata"]["annotations"]["owner"] == "sre"
        and m.get("Service", FULL)["metadata"]["labels"]["env"] == "prod"
        and m.get("ConfigMap", f"{FULL}-config")["metadata"]["annotations"]["owner"] == "sre",
        "common labels and annotations reach every object",
    )
    check(
        "env" not in app["spec"]["selector"]["matchLabels"],
        "but never the selector, which cannot change after install",
    )


def registry_and_pull_secrets() -> None:
    section("options · registry mirror and pull secrets")
    m = render(
        {
            "global": {"imageRegistry": "mirror.example.cn/", "imagePullSecrets": [{"name": "global-pull"}]},
            "imagePullSecrets": [{"name": "local-pull"}],
            "mode": "distributed",
            "web": {"enabled": True, "image": {"repository": "internal/web", "tag": "1"}},
        }
    )
    check(m.returncode == 0, "renders", m.stderr)
    images = {}
    for w in m.workloads():
        for c in containers(w) + containers(w, init=True):
            images[(w["metadata"]["name"], c["name"])] = c["image"]
    wanted = {
        (f"{FULL}-router", "molesignal"): "mirror.example.cn/molesignal/molesignal:latest",
        (f"{FULL}-router", "wait-for-dependencies"): "mirror.example.cn/molesignal/molesignal:latest",
        (f"{FULL}-postgresql", "postgresql"): "mirror.example.cn/postgres:17",
        (f"{FULL}-rustfs", "rustfs"): "mirror.example.cn/rustfs/rustfs:latest",
        (f"{FULL}-rustfs", "bucket-init"): "mirror.example.cn/amazon/aws-cli:latest",
        (f"{FULL}-web", "web"): "mirror.example.cn/internal/web:1",
    }
    check(all(images[k] == v for k, v in wanted.items()), "the registry host is replaced and the path kept", str({k: images[k] for k in wanted if images[k] != wanted[k]}))
    check(
        pod_spec(m.get("Deployment", f"{FULL}-router"))["imagePullSecrets"] == [{"name": "global-pull"}, {"name": "local-pull"}],
        "global and local pull secrets are combined",
    )
    check(all(pod_spec(w).get("imagePullSecrets") for w in m.workloads()), "every workload gets them")
    digest = "sha256:" + "a" * 64
    pinned = render(sets=[f"image.digest={digest}"])
    check(container(pinned.get("StatefulSet", FULL), "molesignal")["image"] == f"ghcr.io/molesignal/molesignal:latest@{digest}", "a digest is appended to the tag")
    custom = render(sets=["image.tag=26.2.0.0", "image.repository=registry.local/ms"])
    check(container(custom.get("StatefulSet", FULL), "molesignal")["image"] == "registry.local/ms:26.2.0.0", "repository and tag can be set")


def extras() -> None:
    section("options · service account, services and extra objects")
    m = render({"serviceAccount": {"create": False, "name": "mine"}})
    check(not m.of("ServiceAccount") and pod_spec(m.get("StatefulSet", FULL))["serviceAccountName"] == "mine", "use an existing service account")
    m = render({"serviceAccount": {"annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/x"}, "automount": True}})
    sa = m.get("ServiceAccount", FULL)
    check(sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"].startswith("arn:") and sa["automountServiceAccountToken"] is True, "service account annotations and automount")
    m = render({"mode": "distributed", "router": {"service": {"type": "LoadBalancer", "annotations": {"a": "b"}}}, "alert": {"service": {"type": "NodePort"}}})
    router = m.get("Service", f"{FULL}-router")
    check(router["spec"]["type"] == "LoadBalancer" and router["metadata"]["annotations"] == {"a": "b"}, "router Service type and annotations")
    check(m.get("Service", f"{FULL}-alert")["spec"]["type"] == "NodePort", "alert Service type")

    m = render(
        {
            "extraObjects": [
                {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "{{ .Release.Name }}-deny", "namespace": "{{ .Release.Namespace }}"}, "spec": {"podSelector": {}}},
            ]
        }
    )
    check(m.returncode == 0 and m.has("NetworkPolicy", "rel-deny"), "extraObjects are templated", m.stderr)
    check(m.get("NetworkPolicy", "rel-deny")["metadata"]["namespace"] == "ns", "and can use the release namespace")

    m = render({"standalone": {"persistence": {"enabled": False}, "persistentVolumeClaimRetentionPolicy": {"whenDeleted": "Delete"}}, "postgresql": {"persistence": {"enabled": False}}, "rustfs": {"persistence": {"enabled": False}}})
    integrity(m, "no persistence")
    for name in (FULL, f"{FULL}-postgresql", f"{FULL}-rustfs"):
        sts = m.get("StatefulSet", name)
        check("volumeClaimTemplates" not in sts["spec"] and {"name": "data", "emptyDir": {}} in pod_spec(sts)["volumes"], f"{name}: emptyDir when persistence is off")
    check(m.get("StatefulSet", FULL)["spec"]["persistentVolumeClaimRetentionPolicy"] == {"whenDeleted": "Delete"}, "PVC retention policy is passed through")

    m = render({"standalone": {"persistence": {"storageClass": "fast", "size": "50Gi", "accessModes": ["ReadWriteOncePod"], "annotations": {"x": "y"}}}, "postgresql": {"persistence": {"storageClass": "-"}}})
    claim = m.get("StatefulSet", FULL)["spec"]["volumeClaimTemplates"][0]
    check(
        claim["spec"]["storageClassName"] == "fast" and claim["spec"]["accessModes"] == ["ReadWriteOncePod"]
        and claim["spec"]["resources"]["requests"]["storage"] == "50Gi" and claim["metadata"]["annotations"] == {"x": "y"},
        "claim storage class, size, modes and annotations",
    )
    check(m.get("StatefulSet", f"{FULL}-postgresql")["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"] == "", "'-' disables dynamic provisioning")
    m = render({"waitForDependencies": {"enabled": False}})
    check(not containers(m.get("StatefulSet", FULL), init=True), "the wait init container can be switched off")
    m = render({"postgresql": {"extraArgs": ["-c", "shared_buffers=512MB"], "maxConnections": 300}})
    check(container(m.get("StatefulSet", f"{FULL}-postgresql"), "postgresql")["args"] == ["-c", "max_connections=300", "-c", "shared_buffers=512MB"], "PostgreSQL flags")
    m = render({"rustfs": {"console": {"enabled": False}}})
    check([p["port"] for p in m.get("Service", f"{FULL}-rustfs")["spec"]["ports"]] == [9000], "RustFS console can be switched off")
    check(env_of(container(m.get("StatefulSet", f"{FULL}-rustfs"), "rustfs"))["RUSTFS_CONSOLE_ENABLE"]["value"] == "false", "and is disabled in the server")
    m = render({"standalone": {"replicaCount": 1, "terminationGracePeriodSeconds": 120}, "releaseChannel": "stable"})
    check(pod_spec(m.get("StatefulSet", FULL))["terminationGracePeriodSeconds"] == 120, "grace period is configurable")
    check(env_of(container(m.get("StatefulSet", FULL), "molesignal"))["RELEASE_CHANNEL"]["value"] == "stable", "release channel is configurable")


SCENARIOS = [scheduling, probes, autoscaling_and_budgets, ingress, web_layer, naming, registry_and_pull_secrets, extras]
