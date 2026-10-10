"""What a plain install creates: standalone, distributed, and with external services."""

from __future__ import annotations

from harness import (
    b64,
    check,
    container,
    containers,
    env_of,
    integrity,
    pod_spec,
    port_names,
    render,
    section,
    unb64,
)

IMAGE = "ghcr.io/molesignal/molesignal:latest"
FULL = "rel-molesignal"


def secret_ref(env: dict, name: str) -> tuple[str, str]:
    ref = env[name]["valueFrom"]["secretKeyRef"]
    return ref["name"], ref["key"]




def standalone_defaults() -> None:
    section("install · standalone with the bundled PostgreSQL and RustFS (defaults)")
    m = render()
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "standalone")

    expected = sorted(
        [
            ("ConfigMap", f"{FULL}-config"),
            ("Pod", f"{FULL}-test-connection"),
            ("Secret", f"{FULL}-postgresql"),
            ("Secret", f"{FULL}-rustfs"),
            ("Secret", f"{FULL}-secret"),
            ("Service", FULL),
            ("Service", f"{FULL}-postgresql"),
            ("Service", f"{FULL}-postgresql-headless"),
            ("Service", f"{FULL}-rustfs"),
            ("Service", f"{FULL}-rustfs-headless"),
            ("ServiceAccount", FULL),
            ("StatefulSet", FULL),
            ("StatefulSet", f"{FULL}-postgresql"),
            ("StatefulSet", f"{FULL}-rustfs"),
        ]
    )
    check(m.kinds() == expected, "creates exactly the expected objects", f"got {m.kinds()}")

    app = m.get("StatefulSet", FULL)
    main = container(app, "molesignal")
    env = env_of(main)
    check(main["image"] == IMAGE, "runs the application image at its latest tag", main["image"])
    check(main["imagePullPolicy"] == "Always", "and pulls it every time, since latest moves")
    check(main["args"] == ["--config", "/app/conf/config.toml"], "starts with the config file")
    check(env["MS_NODE_ROLES"]["value"] == '["standalone"]', "node role is standalone")
    check("MS_CLUSTER_ADVERTISE_ADDR" not in env and "POD_IP" not in env, "does not advertise a peer address")
    check(env["RELEASE_CHANNEL"]["value"] == "alpha", "release channel defaults to alpha")
    check(secret_ref(env, "MS_CIPHER_KEY") == (f"{FULL}-secret", "cipher-key"), "cipher key comes from the core secret")
    check(
        secret_ref(env, "MS_SELF_TELEMETRY_CLUSTER_TOKEN") == (f"{FULL}-secret", "cluster-token"),
        "cluster token comes from the core secret",
    )
    jwt = env["MS_AUTH_JWT_SECRET_OVERRIDE"]["valueFrom"]["secretKeyRef"]
    check(jwt.get("optional") is True, "the JWT override is optional (the application bootstraps its own)")
    check(
        env["MS_META_DATABASE_DSN"]["value"]
        == f"postgres://molesignal:$(DB_PASSWORD)@{FULL}-postgresql:5432/molesignal",
        "database connection string points at the bundled PostgreSQL",
    )
    check(secret_ref(env, "DB_PASSWORD") == (f"{FULL}-postgresql", "password"), "database password comes from its secret")
    check(
        secret_ref(env, "MS_OBJECT_STORE_ACCESS_KEY") == (f"{FULL}-rustfs", "access-key")
        and secret_ref(env, "MS_OBJECT_STORE_SECRET_KEY") == (f"{FULL}-rustfs", "secret-key"),
        "object store credentials come from the RustFS secret",
    )
    check(
        port_names(main) == {"http", "grpc", "otlp-grpc", "probe-register", "probe-control"},
        "listens on every port of the standalone role",
    )
    ready = main["readinessProbe"]
    check(
        ready["httpGet"] == {"path": "/api/v1/readyz", "port": "http"} and ready["failureThreshold"] == 12,
        "readiness waits for WAL replay (readyz, 12 failures allowed)",
    )
    check("livenessProbe" not in main, "no liveness probe: a slow WAL replay must not get the pod killed")
    mounts = {vm["name"]: vm["mountPath"] for vm in main["volumeMounts"]}
    check(mounts == {"config": "/app/conf", "data": "/data"}, "mounts the config and the WAL volume", str(mounts))
    claim = app["spec"]["volumeClaimTemplates"][0]
    check(
        claim["metadata"]["name"] == "data" and claim["spec"]["resources"]["requests"]["storage"] == "20Gi",
        "keeps the WAL on a 20Gi claim",
    )
    check(pod_spec(app)["terminationGracePeriodSeconds"] == 60, "grace period exceeds the 30s drain timeout")
    check(pod_spec(app)["automountServiceAccountToken"] is False, "does not mount the service account token")

    wait = container(app, "wait-for-dependencies")
    script = wait["args"][0]
    check(
        wait["image"] == IMAGE
        and f"wait_tcp {FULL}-postgresql 5432" in script
        and f"wait_http http://{FULL}-rustfs:9000/health" in script,
        "waits for the bundled PostgreSQL and RustFS before starting",
    )

    annotations = app["spec"]["template"]["metadata"]["annotations"]
    check(
        set(annotations) >= {"checksum/config", "checksum/secret"},
        "pods carry config and secret checksums, so changes roll them",
    )
    again = render()
    check(
        again.get("StatefulSet", FULL)["spec"]["template"]["metadata"]["annotations"] == annotations
        and again.get("StatefulSet", f"{FULL}-postgresql")["spec"]["template"]["metadata"]["annotations"]
        == m.get("StatefulSet", f"{FULL}-postgresql")["spec"]["template"]["metadata"]["annotations"],
        "checksums are stable across renders although the generated secrets are not",
    )

    check(
        m.config()
        == {
            "object_store": {
                "backend": "s3",
                "bucket": "molesignal",
                "endpoint": f"http://{FULL}-rustfs:9000",
                "path_style": True,
                "region": "us-east-1",
            },
            "scheduled_reports": {"renderer": {"base_url": f"http://{FULL}:5080"}},
            "telemetry": {"log_format": "json", "log_level": "info,tantivy=warn"},
            "wal": {"dir": "/data/wal"},
        },
        "config.toml carries only what the chart derives",
        str(m.config()),
    )

    core = m.get("Secret", f"{FULL}-secret")
    check(core["metadata"]["annotations"].get("helm.sh/resource-policy") == "keep", "core secret survives uninstall")
    cipher_text = unb64(core["data"]["cipher-key"])
    import base64

    check(len(base64.b64decode(cipher_text)) == 32, "the generated cipher key is 32 bytes of base64")
    check(len(unb64(core["data"]["cluster-token"])) == 48, "the generated cluster token is 48 characters")
    check("jwt-secret-override" not in core["data"], "no JWT override unless asked for")

    pg = m.get("StatefulSet", f"{FULL}-postgresql")
    pgc = container(pg, "postgresql")
    check(pgc["image"] == "postgres:17", "PostgreSQL 17", pgc["image"])
    check("max_connections=200" in pgc["args"], "raises max_connections above the default 100")
    check(env_of(pgc)["PGDATA"]["value"] == "/var/lib/postgresql/data/pgdata", "PGDATA is a subdirectory of the mount")
    check(
        pod_spec(pg)["securityContext"]["fsGroup"] == 999 and pgc["securityContext"]["runAsUser"] == 999,
        "runs as the image's postgres user",
    )
    check(
        pg["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "10Gi",
        "10Gi claim for PostgreSQL",
    )
    check(
        all("pg_isready" in " ".join(pgc[p]["exec"]["command"]) for p in ("startupProbe", "readinessProbe", "livenessProbe")),
        "probes use pg_isready",
    )
    check(pg["spec"]["serviceName"] == f"{FULL}-postgresql-headless", "governed by the headless service")

    fs = m.get("StatefulSet", f"{FULL}-rustfs")
    names = [c["name"] for c in containers(fs)]
    check(names == ["rustfs", "bucket-init"], "RustFS pod has the server and the bucket sidecar", str(names))
    rustfs = container(fs, "rustfs")
    sidecar = container(fs, "bucket-init")
    check(rustfs["image"] == "rustfs/rustfs:latest", "follows RustFS latest", rustfs["image"])
    check(sidecar["image"] == "public.ecr.aws/aws-cli/aws-cli:latest", "follows the AWS CLI latest", sidecar["image"])
    check(
        rustfs["imagePullPolicy"] == "Always" and sidecar["imagePullPolicy"] == "Always",
        "both are pulled every time",
    )
    check(
        pgc["imagePullPolicy"] == "IfNotPresent",
        "PostgreSQL 17 is pinned, so a cached image is fine",
    )
    check(
        "waiting for RustFS (attempt" in sidecar["args"][0],
        "the sidecar says why it is still waiting",
    )
    check(
        sidecar["readinessProbe"]["exec"]["command"] == ["test", "-f", "/tmp/bucket-ready"],
        "the pod is ready only once the bucket exists",
    )
    check(env_of(sidecar)["BUCKET"]["value"] == "molesignal", "creates the configured bucket")
    check(
        pod_spec(fs)["securityContext"]["fsGroup"] == 10001 and rustfs["securityContext"]["runAsUser"] == 10001,
        "runs as the image's rustfs user",
    )
    check(
        {"RUSTFS_VOLUMES", "RUSTFS_ADDRESS", "RUSTFS_CONSOLE_ADDRESS", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY"}
        <= set(env_of(rustfs)),
        "RustFS is configured like the compose service",
    )
    check(rustfs["readinessProbe"]["httpGet"] == {"path": "/health", "port": "s3"}, "RustFS readiness is /health")
    rs = m.get("Service", f"{FULL}-rustfs")
    check([p["port"] for p in rs["spec"]["ports"]] == [9000, 9001], "RustFS service exposes S3 and the console")

    svc = m.get("Service", FULL)
    check(svc["spec"]["type"] == "ClusterIP", "service is ClusterIP by default")
    check(
        [p["name"] for p in svc["spec"]["ports"] if p.get("appProtocol") == "grpc"] == ["grpc", "otlp-grpc"],
        "gRPC ports are marked appProtocol grpc",
    )


def distributed_defaults() -> None:
    section("install · distributed with the bundled services")
    m = render(sets=["mode=distributed"])
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "distributed")

    roles = ("router", "intake", "querier", "compactor", "alert")
    names = {r: f"{FULL}-{r}" for r in roles}
    check(
        m.names("Deployment") == sorted(names[r] for r in ("router", "querier", "compactor", "alert")),
        "router, querier, compactor and alert are Deployments",
    )
    check(
        m.names("StatefulSet") == sorted([names["intake"], f"{FULL}-postgresql", f"{FULL}-rustfs"]),
        "intake is a StatefulSet",
    )
    check(
        sorted(m.names("Service"))
        == sorted(
            [names[r] for r in ("router", "intake", "querier", "alert")]
            + [f"{FULL}-postgresql", f"{FULL}-postgresql-headless", f"{FULL}-rustfs", f"{FULL}-rustfs-headless"]
        ),
        "every role but the compactor has a Service; no standalone objects remain",
    )

    replicas = {r: m.get("StatefulSet" if r == "intake" else "Deployment", names[r])["spec"]["replicas"] for r in roles}
    check(replicas == {"router": 2, "intake": 2, "querier": 2, "compactor": 1, "alert": 1}, "replica counts match deploy/k8s", str(replicas))

    expected_ports = {
        "router": {"http", "otlp-grpc"},
        "intake": {"http", "grpc", "otlp-grpc"},
        "querier": {"http", "grpc"},
        "compactor": {"http"},
        "alert": {"http", "probe-register", "probe-control"},
    }
    expected_probe = {
        "router": ("/api/v1/healthz", 5),
        "intake": ("/api/v1/readyz", 10),
        "querier": ("/api/v1/readyz", 5),
        "compactor": ("/api/v1/readyz", 5),
        "alert": ("/api/v1/readyz", 5),
    }
    for role in roles:
        doc = m.get("StatefulSet" if role == "intake" else "Deployment", names[role])
        main = container(doc, "molesignal")
        env = env_of(main)
        check(env["MS_NODE_ROLES"]["value"] == f'["{role}"]', f"{role}: node role")
        check(port_names(main) == expected_ports[role], f"{role}: ports", str(port_names(main)))
        path, delay = expected_probe[role]
        probe = main["readinessProbe"]
        check(
            probe["httpGet"]["path"] == path and probe["initialDelaySeconds"] == delay,
            f"{role}: readiness probe matches the upstream manifest",
            str(probe),
        )
        if role == "router":
            check("MS_CLUSTER_ADVERTISE_ADDR" not in env, "router: advertises no address, like upstream")
        else:
            check(
                env["MS_CLUSTER_ADVERTISE_ADDR"]["value"] == "$(POD_IP):5082"
                and env["POD_IP"]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP",
                f"{role}: advertises $(POD_IP):5082 to its peers",
            )
        wal = "data" in {vm["name"] for vm in main["volumeMounts"]}
        check(wal == (role == "intake"), f"{role}: WAL volume only on intake")

    intake = m.get("StatefulSet", names["intake"])
    check(m.get("Service", names["intake"])["spec"]["clusterIP"] == "None", "intake Service is headless")
    check(intake["spec"]["serviceName"] == names["intake"], "intake is governed by its own Service")
    check(pod_spec(intake)["terminationGracePeriodSeconds"] == 60, "intake gets time to drain")
    check(
        all("terminationGracePeriodSeconds" not in pod_spec(m.get("Deployment", names[r])) for r in ("router", "querier", "compactor", "alert")),
        "other roles keep the default grace period",
    )
    check(
        intake["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "20Gi",
        "intake keeps a 20Gi claim per replica",
    )
    check(intake["spec"]["podManagementPolicy"] == "OrderedReady", "intake starts replicas one by one")
    check(
        m.config()["scheduled_reports"]["renderer"]["base_url"] == f"http://{FULL}-router:5080",
        "report rendering goes through the router, which serves the UI",
    )
    alert = m.get("Service", names["alert"])
    check([p["port"] for p in alert["spec"]["ports"]] == [5080, 5084, 5085], "alert Service exposes the probe ports")
    router = m.get("Service", names["router"])
    check([p["port"] for p in router["spec"]["ports"]] == [5080, 4317], "router Service exposes the API and OTLP gRPC")


def external_services() -> None:
    section("install · external PostgreSQL and object store")
    values = {
        "postgresql": {"enabled": False},
        "rustfs": {"enabled": False},
        "externalDatabase": {"dsn": "postgres://u:p%40ss@db.example.com:5432/molesignal?sslmode=require"},
        "objectStore": {
            "bucket": "telemetry",
            "region": "eu-west-1",
            "endpoint": "https://s3.example.com",
            "pathStyle": True,
            "accessKey": "AKIAEXAMPLE",
            "secretKey": "s3cr3t/with+chars",
        },
    }
    m = render(values)
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "external")
    check(
        m.kinds()
        == sorted(
            [
                ("ConfigMap", f"{FULL}-config"),
                ("Pod", f"{FULL}-test-connection"),
                ("Secret", f"{FULL}-external"),
                ("Secret", f"{FULL}-secret"),
                ("Service", FULL),
                ("ServiceAccount", FULL),
                ("StatefulSet", FULL),
            ]
        ),
        "no bundled PostgreSQL or RustFS, one extra secret for the inline credentials",
        str(m.kinds()),
    )
    ext = m.get("Secret", f"{FULL}-external")
    check(
        {k: unb64(v) for k, v in ext["data"].items()}
        == {
            "database-dsn": values["externalDatabase"]["dsn"],
            "object-store-access-key": "AKIAEXAMPLE",
            "object-store-secret-key": "s3cr3t/with+chars",
        },
        "stores the connection string and keys verbatim",
    )
    app = m.get("StatefulSet", FULL)
    env = env_of(container(app, "molesignal"))
    check(secret_ref(env, "MS_META_DATABASE_DSN") == (f"{FULL}-external", "database-dsn"), "DSN comes from the external secret")
    check("DB_PASSWORD" not in env, "no separate database password variable")
    check(
        secret_ref(env, "MS_OBJECT_STORE_ACCESS_KEY") == (f"{FULL}-external", "object-store-access-key"),
        "object store key comes from the external secret",
    )
    check(not containers(app, init=True), "nothing to wait for")
    check(
        m.config()["object_store"]
        == {
            "backend": "s3",
            "bucket": "telemetry",
            "endpoint": "https://s3.example.com",
            "path_style": True,
            "region": "eu-west-1",
        },
        "object store settings follow the values",
    )

    aws = render({"postgresql": {"enabled": False}, "rustfs": {"enabled": False}, "externalDatabase": {"dsn": "postgres://x"}})
    check(aws.returncode == 0, "AWS S3 with no endpoint and no keys renders", aws.stderr)
    env = env_of(container(aws.get("StatefulSet", FULL), "molesignal"))
    check("MS_OBJECT_STORE_ACCESS_KEY" not in env, "no credentials variables without keys")
    check("endpoint" not in aws.config()["object_store"], "no endpoint key for AWS S3")
    check(aws.config()["object_store"]["path_style"] is False, "virtual-hosted style for AWS S3")
    check(not aws.has("Secret", f"{FULL}-external") or "object-store-access-key" not in aws.get("Secret", f"{FULL}-external")["data"],
          "no credential secret without keys")


def existing_secrets() -> None:
    section("install · secrets you manage yourself")
    values = {
        "secrets": {"existingSecret": "core", "keys": {"cipherKey": "ck", "clusterToken": "ct", "jwtSecretOverride": "jwt"}},
        "postgresql": {"auth": {"existingSecret": "pg", "secretKey": "pw"}},
        "rustfs": {"auth": {"existingSecret": "s3", "secretKeys": {"accessKey": "a", "secretKey": "s"}}},
    }
    m = render(values)
    check(m.returncode == 0, "renders", m.stderr)
    check(
        m.names("Secret") == [],
        "creates no Secret at all when every secret is yours",
        str(m.names("Secret")),
    )
    env = env_of(container(m.get("StatefulSet", FULL), "molesignal"))
    check(secret_ref(env, "MS_CIPHER_KEY") == ("core", "ck"), "cipher key from your secret and key name")
    check(secret_ref(env, "MS_SELF_TELEMETRY_CLUSTER_TOKEN") == ("core", "ct"), "cluster token from your secret")
    check(secret_ref(env, "MS_AUTH_JWT_SECRET_OVERRIDE") == ("core", "jwt"), "JWT override from your secret")
    check(secret_ref(env, "DB_PASSWORD") == ("pg", "pw"), "database password from your secret")
    check(secret_ref(env, "MS_OBJECT_STORE_ACCESS_KEY") == ("s3", "a"), "object store key from your secret")
    pg = container(m.get("StatefulSet", f"{FULL}-postgresql"), "postgresql")
    check(env_of(pg)["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"] == {"name": "pg", "key": "pw"}, "PostgreSQL reads the same secret")
    rs = container(m.get("StatefulSet", f"{FULL}-rustfs"), "rustfs")
    check(env_of(rs)["RUSTFS_SECRET_KEY"]["valueFrom"]["secretKeyRef"] == {"name": "s3", "key": "s"}, "RustFS reads the same secret")

    ext = render(
        {
            "postgresql": {"enabled": False},
            "rustfs": {"enabled": False},
            "externalDatabase": {"existingSecret": "dbsecret", "existingSecretKey": "dsn"},
            "objectStore": {"existingSecret": "objsecret", "existingSecretKeys": {"accessKey": "ak", "secretKey": "sk"}},
        }
    )
    check(ext.returncode == 0, "external services through existing secrets render", ext.stderr)
    check(not ext.has("Secret", f"{FULL}-external"), "no external secret is created")
    env = env_of(container(ext.get("StatefulSet", FULL), "molesignal"))
    check(secret_ref(env, "MS_META_DATABASE_DSN") == ("dbsecret", "dsn"), "DSN from your secret")
    check(secret_ref(env, "MS_OBJECT_STORE_SECRET_KEY") == ("objsecret", "sk"), "object store key from your secret")
    _ = b64


SCENARIOS = [standalone_defaults, distributed_defaults, external_services, existing_secrets]
