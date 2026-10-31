# How the chart is put together

## Relation to the application's own manifests

The application repository ships plain manifests in `deploy/k8s`. The chart is built from them:
the same roles, ports, probes and environment variables, so that an install from the chart
behaves like one from those manifests. Where the chart differs it is on purpose, and listed in
[what the chart adds](#what-the-chart-adds).

## Roles

All roles run one image and differ in `MS_NODE_ROLES`. Nodes discover each other through
PostgreSQL, where each node registers `MS_CLUSTER_ADVERTISE_ADDR` (`$(POD_IP):5082`).

| Role | Workload | Replicas | Ports | Readiness | Service |
| --- | --- | --- | --- | --- | --- |
| `standalone` | StatefulSet + PVC (`/data/wal`) | 1 | 5080 http, 5082 grpc, 4317 otlp-grpc, 5084/5085 probe agents | `/api/v1/readyz` | `<fullname>` |
| `router` | Deployment | 2 | 5080, 4317 | `/api/v1/healthz` | `<fullname>-router` |
| `intake` | StatefulSet + PVC (`/data/wal`) | 2 | 5080, 5082, 4317 | `/api/v1/readyz` | headless |
| `querier` | Deployment | 2 | 5080, 5082 | `/api/v1/readyz` | `<fullname>-querier` |
| `compactor` | Deployment | 1 | 5080 | `/api/v1/readyz` | none |
| `alert` | Deployment | 1 | 5080, 5084, 5085 | `/api/v1/readyz` | `<fullname>-alert` |

The router does not advertise an address (it serves the HTTP API and forwards), exactly as in the
upstream manifests. There is deliberately **no liveness probe**: a slow write-ahead-log replay must
not get an intake pod killed.

The role table lives in one place, `molesignal.roleSpecs` in
[`_helpers.tpl`](../charts/molesignal/templates/_helpers.tpl); the workload, Service, HPA and
budget templates loop over it.

## Configuration

```
values ─┬─ logging, externalUrl, objectStore, rustfs, web ──► derived keys ─┐
        └─ config (yours) ────────────────────────────────────────────────► deep merge ─► config.toml (ConfigMap)
```

- The chart derives a few keys (`wal.dir`, `object_store.*`, `telemetry.*`,
  `http.external_url`, `scheduled_reports.renderer.base_url`) and merges `config` over them, so
  yours win. Everything else is the application's built-in default, which the sample
  `conf/config.toml` documents.
- The TOML is written by the chart's own template rather than Helm's `toToml`: values read from a
  YAML file reach templates as floats, `toToml` prints them as `86400.0`, and the application's
  integer settings reject that. `tests/scenarios_config.py` pins the behaviour.
- **Secrets never go into the ConfigMap.** The database connection string, object-store keys,
  cipher key, cluster token and JWT override are environment variables read from Secrets. The
  application gives `MS_*` variables precedence over the file.
- `MS_META_DATABASE_DSN` for the bundled PostgreSQL is built in the pod from `$(DB_PASSWORD)`, so
  the password is never written into a manifest.

## Secrets

Generated values are read back from the cluster with `lookup` on every upgrade, so they stay
stable; they carry `helm.sh/resource-policy: keep` so that the cipher key outlives the release.
The pod annotations carry a checksum of the values *you* set, never of generated ones: rotating a
value you control rolls the pods, an ordinary upgrade does not.

`lookup` cannot run under `helm template`. The tests therefore run the same secret logic in a copy
of the chart where the one `lookup` call reads from a test fixture (`tests/harness.py`).

## The bundled PostgreSQL and RustFS

Both are single-instance StatefulSets for evaluation. Details that matter:

- **PostgreSQL** runs as the image's `postgres` user (uid 999) with `fsGroup: 999`, keeps its data
  in the `pgdata` subdirectory of the volume (a fresh volume can contain `lost+found`), and starts
  with `max_connections=200`, because every MoleSignal process holds its own connection pool (16
  by default), which exceeds PostgreSQL's default 100 in a distributed install.
- **RustFS** runs as uid 10001. A `bucket-init` sidecar in the same pod does what the compose
  `rustfs-init` service does (create the bucket, remove any bucket policy) and its readiness is
  "the bucket exists". The pod, and therefore the Service, is ready only then. Everything waits for
  it, so its image has to pull reliably: it comes from Docker Hub (`amazon/aws-cli`) like the chart's
  other public images, because ECR Public, where the compose file gets the same image, answered
  anonymous pulls from CI's shared addresses with `429 Too Many Requests` for the whole install.
- **MoleSignal pods** wait in an init container until the PostgreSQL port accepts connections and
  RustFS's `/health` answers through its Service, instead of crash-looping while those start. The
  init container uses the application image, which has `bash` and `curl`.

## What the chart adds

Relative to `deploy/k8s`:

- both topologies (`standalone`, which the compose file calls a profile, and `distributed`);
- optional PostgreSQL and RustFS;
- `terminationGracePeriodSeconds: 60` for roles with a write-ahead log, so they can finish the
  30-second drain the application allows itself;
- generated secrets instead of the all-zero development cipher key and fixed cluster token of
  `20-secret.yaml`;
- optional HPA and PodDisruptionBudget, ingress for the UI and for OTLP gRPC, scheduling and
  security-context values;
- the report-rendering URL points at the router (which serves the embedded UI) instead of an
  nginx `web` Service that most installs do not run.

## What the chart does not do

- Run PostgreSQL or the object store **highly available**. Use managed services.
- Wire up `NetworkPolicy`, a `ServiceMonitor` or a `PrometheusRule` (use `extraObjects`, or
  `podAnnotations` for annotation-based scraping: metrics are at `/metrics` on port 5080).
- Expose the Arrow Flight SQL listener (port 5083, off by default in the application).
- Configure TLS inside the application (`http.tls`); terminate TLS at the ingress.
- Support Azure or GCS object stores through dedicated values. They can be configured through
  `config.object_store` and `extraEnv`, but are not tested.
- Use IAM roles for service accounts or EKS Pod Identity. The application builds its S3 client
  with `AmazonS3Builder::new()`, so the web-identity and pod-identity variables of the `AWS_*`
  environment are never read; without keys the client falls back to the instance metadata
  service, that is, an instance profile on the node. Run with keys, or with an instance profile.
