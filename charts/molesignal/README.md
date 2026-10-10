# MoleSignal Helm chart

[MoleSignal](https://www.molesignal.com) is self-hosted, OpenTelemetry-native observability:
logs, metrics and traces on one storage layer (Parquet on an object store), one query
engine (DataFusion) and one metadata database (PostgreSQL). This chart deploys it on
Kubernetes.

The chart has its own version, separate from the application's (see
[versioning](https://github.com/molesignal/molesignal-helm/blob/main/docs/versioning.md)):
`helm show chart` prints both `version` (the chart) and `appVersion` (the default image).

## TL;DR

```bash
helm repo add molesignal https://charts.molesignal.com
helm repo update

# One MoleSignal pod plus a bundled PostgreSQL and RustFS: enough to try it out.
helm install molesignal molesignal/molesignal \
  --namespace molesignal --create-namespace

kubectl --namespace molesignal port-forward svc/molesignal 5080:5080
# http://127.0.0.1:5080
```

or from the OCI registry:

```bash
helm install molesignal oci://ghcr.io/molesignal/charts/molesignal --version 0.1.0 \
  --namespace molesignal --create-namespace
```

`helm test molesignal --namespace molesignal` checks that the API answers.

## Requirements

- Kubernetes 1.27 or later and Helm 3.8 or later (3.8 for OCI).
- A default `StorageClass`, or one named in the values, for the write-ahead log and the
  bundled services (or switch persistence off for a throw-away install).
- For production: a PostgreSQL 17 database and an S3-compatible bucket.

## Images

Every image except PostgreSQL's follows `latest` and is pulled on every pod start
(`pullPolicy: Always`, because `IfNotPresent` would keep serving a stale `latest` from the node's cache):

| Value | Default |
| --- | --- |
| `image` (the application) | `ghcr.io/molesignal/molesignal:latest` |
| `rustfs.image` | `rustfs/rustfs:latest` |
| `rustfs.bucketInit.image` | `public.ecr.aws/aws-cli/aws-cli:latest` |
| `postgresql.image` | `postgres:17` |

`latest` moves, so two installs of the same chart can run different versions, and a pod that
restarts can come back on a newer image than its siblings. For anything beyond trying the chart out,
pin `image.tag` (or `image.digest`) and the RustFS tag, and go back to `pullPolicy: IfNotPresent`.

**The chart follows the application's `main` branch**: the role names (`alert`, not `alert_manager`),
the metadata database connection (`MS_META_DATABASE_DSN`), the cluster token and the report-rendering
settings are those of `deploy/k8s` there. An image built before that does not start with this chart.
As of 2026-10-11 that is every tag published for the application (`26.1.0.0`, `alpha`, built on
2026-08-17): the alert role is still called `alert_manager` in them and the metadata store is
configured differently. The application's release workflow also pushes `latest` only for
stable-channel releases, so until there is one `ghcr.io/molesignal/molesignal:latest` cannot be pulled
at all; install with an explicit `image.tag` meanwhile.

## Choosing a topology

| `mode` | What runs | Use it for |
| --- | --- | --- |
| `standalone` (default) | One StatefulSet running every role in a single process, like `docker compose up`. | Trying it out, small installs. Does not scale out. |
| `distributed` | `router`, `intake` (StatefulSet with a write-ahead log per replica), `querier`, `compactor` and `alert`, each its own workload. | Production. The router and querier scale with an HPA; intake, compactor and alert scale by replicas. |

Changing `mode` on a running install replaces the workloads: the `standalone` StatefulSet goes and
the distributed ones appear, with their own volumes. The application flushes pending data when it
is stopped normally (up to `node.drain_timeout_secs`, 30 seconds by default), so stop sending data
and let the old pod terminate on its own before you switch. Data that was already flushed to the
object store stays in the database and the bucket, which both topologies share.

All roles run the same image and differ only in `MS_NODE_ROLES`. Nodes find each other through
PostgreSQL, so there is nothing to wire up between the workloads. The layout mirrors the
manifests in the application repository (`deploy/k8s`).

## The bundled PostgreSQL and RustFS

By default the chart also deploys a single PostgreSQL 17 and a single RustFS (an S3-compatible
object store), and creates the bucket, so that `helm install` is enough to get a working system.
**They are for evaluation.** One replica each, no backups, no failover.

For production turn both off and point the chart at managed services
(see [`examples/production.yaml`](https://github.com/molesignal/molesignal-helm/blob/main/examples/production.yaml)):

```yaml
postgresql:
  enabled: false
externalDatabase:
  existingSecret: molesignal-db      # holds the key `database-dsn`

rustfs:
  enabled: false
objectStore:
  bucket: example-telemetry
  region: eu-west-1
  existingSecret: molesignal-s3      # holds `access-key` and `secret-key`
```

Leave `objectStore.endpoint` empty for AWS S3. For another S3-compatible store set `endpoint`
and, usually, `pathStyle: true`.

## Secrets

| Secret | Holds | If you do not set it |
| --- | --- | --- |
| core (`<release>-molesignal-secret`) | the field-level **cipher key**, the node-to-node **cluster token**, an optional JWT override | generated on first install, read back on every upgrade |
| PostgreSQL | the database password (bundled PostgreSQL only) | generated |
| RustFS | the access and secret key (bundled RustFS only) | generated |
| external | inline `externalDatabase.dsn` / `objectStore` keys | only created when you set them inline |

Generated Secrets carry `helm.sh/resource-policy: keep`, so they survive `helm uninstall`.
That is deliberate: the data on the volumes is only readable with the same keys.

> **Back up the cipher key.** Without it, encrypted fields cannot be read again, even from a
> restored database:
>
> ```bash
> kubectl -n molesignal get secret molesignal-secret \
>   -o jsonpath='{.data.cipher-key}' | base64 --decode
> ```

> **Argo CD, and any tool that runs `helm template`.** Generated values are looked up in the
> cluster, which `helm template` cannot do, so they would change on every sync. Set
> `secrets.cipherKey` and `secrets.clusterToken` yourself, or point `secrets.existingSecret`
> (and the other `existingSecret` values) at Secrets you manage.

## Exposing it

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: observability.example.com
      paths: [{path: /}]
  tls:
    - secretName: observability-tls
      hosts: [observability.example.com]
```

- **OTLP over gRPC** (port 4317) can be reached in the cluster at
  `<release>-molesignal-router:4317` (`<release>-molesignal:4317` in standalone mode). To expose
  it, set `router.service.type: LoadBalancer` (which publishes the HTTP port 5080 too), or enable
  `otlpIngress` and use a controller that speaks gRPC.
- **Live tail** streams rows as they arrive: tell your controller not to buffer responses and
  to allow long reads. [`examples/ingress-nginx.yaml`](https://github.com/molesignal/molesignal-helm/blob/main/examples/ingress-nginx.yaml)
  has the nginx settings.
- **`/metrics` has no authentication.** The application serves it outside its auth layer on
  port 5080, the same port as the UI and API. Do not route it to the internet: block it at the
  ingress (the nginx example shows how), or keep the ingress internal.
- **Probe agents** connect to ports 5084 (register) and 5085 (control) of the alert role. Set
  `alert.service.type` (`standalone.service.type` in standalone mode) to `LoadBalancer` or
  `NodePort`, and tell the application which public addresses to print in the register command:

  ```yaml
  config:
    probe:
      register_endpoint: https://probes.example.com:5084
      control_endpoint: https://probes.example.com:5085
      server_names: [probes.example.com]
  ```

## Configuring the application

The chart writes `config.toml` from what it knows (object store, write-ahead log directory, log
format, report-rendering URL, external URL) and merges your `config` over it. The layout is that
of the application's own `conf/config.toml`, which lists every setting with its default:

```yaml
config:
  compactor:
    retention_days: 90
  cache:
    object:
      max_bytes: 5368709120
```

Everything you do not set uses the application's built-in default. Any setting can also be given
as an environment variable, `MS_<SECTION>_<KEY>`, through `extraEnv`; environment variables win
over the file. Unknown keys in `[object_store]` stop the application from starting, so check
names against the reference file.

The cache of object-store blocks (`cache.object`, 10 GiB by default) lives in the container's
writable layer. On small nodes lower `cache.object.max_bytes`, or set an `ephemeral-storage`
limit that fits.

## Sizing and scheduling

`resources` are empty by default so that the chart installs on a small cluster; set them for
anything real. Intake buffers up to 1 GiB per replica (`intake.max_buffer_memory_mb`) before it
flushes, the alert role renders reports with a headless Chromium, and queriers read Parquet
files, so give those three memory first. HPA targets are percentages of the *requests*.

The application image runs as root and the chart does not change that, so a namespace that enforces
the `restricted` Pod Security Standard rejects the MoleSignal pods (the bundled PostgreSQL and
RustFS are configured to comply). Running the application as another user has not been verified:
it writes its object cache below `/app/data` and the write-ahead log below `/data`.

Every role takes the scheduling values of the top level (`nodeSelector`, `tolerations`, `affinity`,
`topologySpreadConstraints`, `priorityClassName`, security contexts, pod annotations and labels);
a role's own value replaces the global one rather than merging with it.

## Upgrading

```bash
helm repo update
helm upgrade molesignal molesignal/molesignal --namespace molesignal --reuse-values   # or --values your.yaml
```

Prefer `--values your.yaml` to `--reuse-values`: new chart versions add values, and reused
values hide their defaults. Read the chart's [changelog](https://github.com/molesignal/molesignal-helm/blob/main/CHANGELOG.md)
and the application's release notes first. A StatefulSet cannot change its volume claim
template, so resizing a volume means editing the PVC, not the values.

To move to another application version without waiting for a new chart, set `image.tag`. To
promote a build through the delivery channels (`alpha`, `beta`, `rc`, `stable`) change
`releaseChannel`; it is runtime metadata, and the image stays the same.

## Uninstalling

```bash
helm uninstall molesignal --namespace molesignal
```

removes the workloads and services. It keeps, on purpose, the volumes of the StatefulSets
(the write-ahead log, and the bundled PostgreSQL and RustFS data) and the generated Secrets. To
remove everything, including the data:

```bash
kubectl --namespace molesignal delete pvc --selector app.kubernetes.io/instance=molesignal
kubectl --namespace molesignal delete secret molesignal-secret molesignal-postgresql molesignal-rustfs
```

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Pods stay `Pending` | `kubectl describe pvc`: no default StorageClass, or none that fits. Set `*.persistence.storageClass`. |
| A pod sits in `Init:0/1` | The init container `wait-for-dependencies` is waiting for the bundled PostgreSQL or RustFS. `kubectl logs <pod> -c wait-for-dependencies`. |
| The RustFS pod is `1/2` | The `bucket-init` sidecar has not created the bucket yet: `kubectl logs <pod> -c bucket-init`. |
| MoleSignal restarts at startup | Every role checks the object store with a write and a read before it serves. `kubectl logs <pod>` names the failing call; check endpoint, bucket, region, `pathStyle` and the keys. |
| `helm install` fails with `molesignal:` | A guard refused the values; the message says what to change. |
| Nothing arrives | An intake node starts listening only after it has replayed its write-ahead log, and `/api/v1/readyz` passes once it can take writes. `kubectl get pods` should show it `1/1`. |

## Values

The table is generated from `values.yaml` (`python3 scripts/gen-values-doc.py`).

<!-- values:start -->
| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `global.imageRegistry` | string | `""` | Replace the registry host of every image the chart deploys (mirror or air-gapped registry). `ghcr.io/molesignal/molesignal` becomes `<imageRegistry>/molesignal/molesignal`. |
| `global.imagePullSecrets` | list | `[]` | Pull secrets for every pod the chart creates. Merged with `imagePullSecrets`. |
| `nameOverride` | string | `""` | Override the chart name used in resource names and labels. |
| `fullnameOverride` | string | `""` | Override the prefix of every resource name (default: the release name). Kept to 43 characters so that suffixes such as `-postgresql` still fit. |
| `commonLabels` | object | `{}` | Labels added to every resource. |
| `commonAnnotations` | object | `{}` | Annotations added to every resource. |
| `mode` | string | `"standalone"` | Deployment topology. `standalone` runs every role in one StatefulSet (what `docker compose up` does). `distributed` runs router, intake, querier, compactor and alert as separate workloads that scale independently. |
| `image.repository` | string | `"ghcr.io/molesignal/molesignal"` | Application image. One image serves every role. |
| `image.tag` | string | `""` | Image tag. Empty means the chart's `appVersion`, which is `latest`. That tag moves: pin a version (or `digest`) for anything but trying the chart out. |
| `image.digest` | string | `""` | Image digest (`sha256:...`). When set it is used together with the tag. |
| `image.pullPolicy` | string | `"Always"` | Image pull policy. `Always`, because the default tag moves; use `IfNotPresent` once you pin one. |
| `imagePullSecrets` | list | `[]` | Pull secrets, as a list of `{name: ...}`. |
| `releaseChannel` | string | `"alpha"` | Runtime delivery channel, exposed to the application as `RELEASE_CHANNEL`. One of `alpha`, `beta`, `rc`, `stable`. It is metadata only: promoting a build through channels does not change the image, just this value. |
| `logging.level` | string | `"info,tantivy=warn"` | Log filter, in `tracing` env-filter syntax. |
| `logging.format` | string | `"json"` | Log format: `json` or `text`. |
| `externalUrl` | string | `""` | Public URL of the UI, for example `https://molesignal.example.com`. Shown in the data-source setup pages and used as the CNAME target for status pages. Empty derives it from the first `ingress.hosts` entry when the ingress is enabled. |
| `config` | object | `{}` | Extra application settings, merged over the chart's own and written to `config.toml`. Mirrors the TOML layout of the application's `conf/config.toml`: a key `compactor.retention_days` becomes `[compactor] retention_days = ...`. Values set here win over the keys the chart derives from its other values (`wal.dir`, `object_store.*`, `telemetry.*`, `http.external_url`, `scheduled_reports.renderer.base_url`). Unknown keys can stop the application from starting; check the reference file shipped with the application. |
| `extraEnv` | list | `[]` | Extra environment variables for every MoleSignal container. Any setting can also be given as `MS_<SECTION>_<KEY>` (for example `MS_COMPACTOR_RETENTION_DAYS`); environment variables win over `config.toml`. |
| `extraEnvFrom` | list | `[]` | Extra `envFrom` sources for every MoleSignal container. |
| `extraVolumes` | list | `[]` | Extra volumes for every MoleSignal pod. |
| `extraVolumeMounts` | list | `[]` | Extra volume mounts for every MoleSignal container. |
| `podAnnotations` | object | `{}` | Annotations added to every MoleSignal pod. |
| `podLabels` | object | `{}` | Labels added to every MoleSignal pod. |
| `podSecurityContext` | object | `{}` | Pod security context for every MoleSignal pod. Empty keeps the image defaults (the application image runs as root). |
| `securityContext` | object | `{}` | Container security context for every MoleSignal container. |
| `nodeSelector` | object | `{}` | Node selector for every MoleSignal pod. A role can override it with its own `nodeSelector`; role values replace these, they are not merged. |
| `tolerations` | list | `[]` | Tolerations for every MoleSignal pod (a role can override). |
| `affinity` | object | `{}` | Affinity for every MoleSignal pod (a role can override). |
| `topologySpreadConstraints` | list | `[]` | Topology spread constraints for every MoleSignal pod (a role can override). The chart does not add the label selector for you; use `app.kubernetes.io/instance` and `app.kubernetes.io/component`. |
| `priorityClassName` | string | `""` | Priority class for every MoleSignal pod (a role can override). |
| `serviceAccount.create` | bool | `true` | Create a service account. |
| `serviceAccount.name` | string | `""` | Name of the service account. Defaults to the full name. |
| `serviceAccount.annotations` | object | `{}` | Annotations for the service account. |
| `serviceAccount.automount` | bool | `false` | Mount the service account token into the pods. The application does not call the Kubernetes API, so it is off by default. |
| `secrets.existingSecret` | string | `""` | Use your own Secret for the cipher key, cluster token and JWT override instead of the one the chart generates. See `secrets.keys` for the key names. |
| `secrets.keys.cipherKey` | string | `"cipher-key"` | Key holding the field-level encryption root key (32 bytes, base64). |
| `secrets.keys.clusterToken` | string | `"cluster-token"` | Key holding the shared token that authenticates node-to-node trace and telemetry forwarding. |
| `secrets.keys.jwtSecretOverride` | string | `"jwt-secret-override"` | Key holding the optional JWT signing secret override. |
| `secrets.cipherKey` | string | `""` | Field-level encryption root key: 32 random bytes, base64 encoded (`openssl rand -base64 32`). Generated on first install when empty and kept on upgrades. **Back it up and keep it for the life of the data**: losing it makes encrypted fields unreadable. Set it explicitly when you render with `helm template` (Argo CD, Flux with post-render), because values generated at render time are not stable there. |
| `secrets.clusterToken` | string | `""` | Shared token for node-to-node forwarding. Generated when empty. |
| `secrets.jwtSecretOverride` | string | `""` | Pin the JWT signing secret. Leave empty: the application generates one on first start and keeps it in the database. Set it (>= 32 bytes, base64) only to share one secret across clusters. |
| `postgresql.enabled` | bool | `true` | Deploy a single PostgreSQL instance with the chart. **For evaluation only**: one replica, no backups, no failover. Turn it off and use `externalDatabase` in production. |
| `postgresql.image.repository` | string | `"postgres"` | PostgreSQL image. |
| `postgresql.image.tag` | string | `"17"` | PostgreSQL image tag. MoleSignal is developed against PostgreSQL 17. |
| `postgresql.image.digest` | string | `""` | Image digest. |
| `postgresql.image.pullPolicy` | string | `"IfNotPresent"` | Image pull policy. |
| `postgresql.auth.username` | string | `"molesignal"` | Database user. Letters, digits and underscores only. |
| `postgresql.auth.database` | string | `"molesignal"` | Database name. Letters, digits and underscores only. |
| `postgresql.auth.password` | string | `""` | Password of the database user. Generated when empty and kept on upgrades. Must use only URL-safe characters (letters, digits, `-._~`) because it is embedded in the connection string. |
| `postgresql.auth.existingSecret` | string | `""` | Use an existing Secret for the password (same URL-safe rule). |
| `postgresql.auth.secretKey` | string | `"password"` | Key of the password inside `existingSecret` (the generated Secret uses it too). |
| `postgresql.maxConnections` | int | `200` | `max_connections`. Every MoleSignal process keeps its own pool (16 by default), so a distributed install needs well above PostgreSQL's default of 100. |
| `postgresql.extraArgs` | list | `[]` | Additional server flags, for example `["-c", "shared_buffers=512MB"]`. |
| `postgresql.persistence.enabled` | bool | `true` | Keep data on a PersistentVolumeClaim. When false an `emptyDir` is used and data is lost whenever the pod restarts. |
| `postgresql.persistence.size` | string | `"10Gi"` | Volume size. |
| `postgresql.persistence.storageClass` | string | `""` | Storage class. Empty uses the cluster default. |
| `postgresql.persistence.accessModes` | list | `["ReadWriteOnce"]` | Access modes. |
| `postgresql.resources` | object | `{}` | Resource requests and limits. |
| `postgresql.podSecurityContext` | object | `{"fsGroup":999,"fsGroupChangePolicy":"OnRootMismatch"}` | Pod security context. The official image runs as uid/gid 999. |
| `postgresql.securityContext` | object | `{"runAsNonRoot":true,"runAsUser":999,"runAsGroup":999,"allowPrivilegeEscalati...` | Container security context. |
| `postgresql.service.type` | string | `"ClusterIP"` | Service type. |
| `postgresql.service.annotations` | object | `{}` | Service annotations. |
| `postgresql.podAnnotations` | object | `{}` | Pod annotations. |
| `postgresql.nodeSelector` | object | `{}` | Node selector. |
| `postgresql.tolerations` | list | `[]` | Tolerations. |
| `postgresql.affinity` | object | `{}` | Affinity. |
| `postgresql.priorityClassName` | string | `""` | Priority class. |
| `externalDatabase.dsn` | string | `""` | Connection string of an external PostgreSQL, for example `postgres://molesignal:secret@db.example.com:5432/molesignal?sslmode=require`. Used when `postgresql.enabled` is false. Stored in a Secret created by the chart. |
| `externalDatabase.existingSecret` | string | `""` | Use an existing Secret that holds the connection string instead of `dsn`. |
| `externalDatabase.existingSecretKey` | string | `"database-dsn"` | Key of the connection string inside `existingSecret`. |
| `rustfs.enabled` | bool | `true` | Deploy a single RustFS instance (S3-compatible object store) and create the bucket in it. **For evaluation only**: one replica, no redundancy. Turn it off and use `objectStore` with S3 or a managed store in production. |
| `rustfs.image.repository` | string | `"rustfs/rustfs"` | RustFS image. |
| `rustfs.image.tag` | string | `"latest"` | RustFS image tag. `latest` moves: pin one for anything but trying the chart out. |
| `rustfs.image.digest` | string | `""` | Image digest. |
| `rustfs.image.pullPolicy` | string | `"Always"` | Image pull policy (`Always` because of the default tag). |
| `rustfs.auth.accessKey` | string | `""` | Root access key. Generated when empty and kept on upgrades. |
| `rustfs.auth.secretKey` | string | `""` | Root secret key. Generated when empty and kept on upgrades. |
| `rustfs.auth.existingSecret` | string | `""` | Use an existing Secret for the root credentials. |
| `rustfs.auth.secretKeys` | object | `{"accessKey":"access-key","secretKey":"secret-key"}` | Key names inside the credentials Secret (the generated Secret uses them too). |
| `rustfs.console.enabled` | bool | `true` | Serve the RustFS web console on port 9001 of the Service. |
| `rustfs.persistence.enabled` | bool | `true` | Keep data on a PersistentVolumeClaim. When false an `emptyDir` is used. |
| `rustfs.persistence.size` | string | `"20Gi"` | Volume size. |
| `rustfs.persistence.storageClass` | string | `""` | Storage class. Empty uses the cluster default. |
| `rustfs.persistence.accessModes` | list | `["ReadWriteOnce"]` | Access modes. |
| `rustfs.bucketInit.image` | object | `{"repository":"public.ecr.aws/aws-cli/aws-cli","tag":"latest","digest":"","pu...` | Image of the sidecar that creates the bucket and keeps it private. The pod only becomes ready once the bucket exists. |
| `rustfs.bucketInit.resources` | object | `{}` | Resource requests and limits of the sidecar. |
| `rustfs.resources` | object | `{}` | Resource requests and limits. |
| `rustfs.podSecurityContext` | object | `{"fsGroup":10001,"fsGroupChangePolicy":"OnRootMismatch"}` | Pod security context. The image runs as uid/gid 10001. |
| `rustfs.securityContext` | object | `{"runAsNonRoot":true,"runAsUser":10001,"runAsGroup":10001,"allowPrivilegeEsca...` | Container security context (applies to RustFS and the sidecar). |
| `rustfs.service.type` | string | `"ClusterIP"` | Service type. |
| `rustfs.service.annotations` | object | `{}` | Service annotations. |
| `rustfs.podAnnotations` | object | `{}` | Pod annotations. |
| `rustfs.nodeSelector` | object | `{}` | Node selector. |
| `rustfs.tolerations` | list | `[]` | Tolerations. |
| `rustfs.affinity` | object | `{}` | Affinity. |
| `rustfs.priorityClassName` | string | `""` | Priority class. |
| `objectStore.bucket` | string | `"molesignal"` | Bucket that holds the data. With the bundled RustFS the chart creates it; otherwise it must already exist. |
| `objectStore.region` | string | `"us-east-1"` | Region. Always `us-east-1` with the bundled RustFS. |
| `objectStore.endpoint` | string | `""` | Endpoint of an S3-compatible store (`https://s3.example.com`). Leave empty for AWS S3. Must be empty when the bundled RustFS is enabled. |
| `objectStore.pathStyle` | bool | `false` | Use path-style requests (`endpoint/bucket/key`). Needed by most S3-compatible stores (MinIO, Ceph, RustFS); AWS S3 wants this off. |
| `objectStore.accessKey` | string | `""` | Access key. Stored in a Secret created by the chart. Leave both keys empty to run without static credentials; whether the application then finds credentials depends on the environment (for example an EC2 instance profile). |
| `objectStore.secretKey` | string | `""` | Secret key. |
| `objectStore.existingSecret` | string | `""` | Use an existing Secret that holds the credentials instead of the two values above. |
| `objectStore.existingSecretKeys` | object | `{"accessKey":"access-key","secretKey":"secret-key"}` | Key names inside `existingSecret`. |
| `standalone.replicaCount` | int | `1` | Replicas. Scaling a standalone install is not supported; use `distributed`. |
| `standalone.resources` | object | `{}` | Resource requests and limits. |
| `standalone.terminationGracePeriodSeconds` | int | `60` | Seconds the kubelet waits after SIGTERM before it kills the process. Keep it above the application's drain timeout (`node.drain_timeout_secs`, 30 by default), which is how long pending data may take to flush. |
| `standalone.persistence.enabled` | bool | `true` | Keep the write-ahead log on a PersistentVolumeClaim. When false an `emptyDir` is used and un-flushed data is lost whenever the pod restarts. |
| `standalone.persistence.size` | string | `"20Gi"` | Volume size. |
| `standalone.persistence.storageClass` | string | `""` | Storage class. Empty uses the cluster default. |
| `standalone.persistence.accessModes` | list | `["ReadWriteOnce"]` | Access modes. |
| `standalone.persistence.annotations` | object | `{}` | Annotations of the PVC. |
| `standalone.persistentVolumeClaimRetentionPolicy` | object | `{}` | What happens to the PVC when the StatefulSet is deleted or scaled down (`whenDeleted` / `whenScaled`: `Retain` or `Delete`). Empty keeps the cluster default. |
| `standalone.service.type` | string | `"ClusterIP"` | Service type. |
| `standalone.service.annotations` | object | `{}` | Service annotations. |
| `router.replicaCount` | int | `2` | Replicas. Ignored when `autoscaling.enabled`. |
| `router.resources` | object | `{}` | Resource requests and limits. |
| `router.service.type` | string | `"ClusterIP"` | Service type. Use `LoadBalancer` to expose the UI/API and OTLP gRPC directly. |
| `router.service.annotations` | object | `{}` | Service annotations. |
| `router.autoscaling.enabled` | bool | `false` | Create a HorizontalPodAutoscaler. |
| `router.autoscaling.minReplicas` | int | `2` | Minimum replicas. |
| `router.autoscaling.maxReplicas` | int | `10` | Maximum replicas. |
| `router.autoscaling.targetCPUUtilizationPercentage` | int | `70` | Target average CPU utilization (percent of the request). |
| `router.autoscaling.targetMemoryUtilizationPercentage` | null | `null` | Target average memory utilization (percent of the request). |
| `router.autoscaling.behavior` | object | `{}` | Scaling behavior, passed through to the HPA. |
| `router.podDisruptionBudget.enabled` | bool | `false` | Create a PodDisruptionBudget. |
| `router.podDisruptionBudget.minAvailable` | null | `null` | Minimum available pods. Set one of `minAvailable` / `maxUnavailable`. |
| `router.podDisruptionBudget.maxUnavailable` | int | `1` | Maximum unavailable pods. |
| `intake.replicaCount` | int | `2` | Replicas. |
| `intake.resources` | object | `{}` | Resource requests and limits. Every replica buffers up to `intake.max_buffer_memory_mb` (1 GiB by default) before it flushes. |
| `intake.terminationGracePeriodSeconds` | int | `60` | Seconds the kubelet waits after SIGTERM before it kills the process (see `standalone`). |
| `intake.podManagementPolicy` | string | `"OrderedReady"` | `OrderedReady` waits for each replica's WAL replay before it starts the next; `Parallel` starts them together. |
| `intake.persistence.enabled` | bool | `true` | Keep each replica's write-ahead log on its own PersistentVolumeClaim. When false an `emptyDir` is used and un-flushed data is lost whenever a pod restarts. |
| `intake.persistence.size` | string | `"20Gi"` | Volume size per replica. |
| `intake.persistence.storageClass` | string | `""` | Storage class. Empty uses the cluster default. |
| `intake.persistence.accessModes` | list | `["ReadWriteOnce"]` | Access modes. |
| `intake.persistence.annotations` | object | `{}` | Annotations of the PVCs. |
| `intake.persistentVolumeClaimRetentionPolicy` | object | `{}` | What happens to the PVCs when the StatefulSet is deleted or scaled down (see `standalone`). |
| `intake.podDisruptionBudget.enabled` | bool | `false` | Create a PodDisruptionBudget. |
| `intake.podDisruptionBudget.minAvailable` | null | `null` | Minimum available pods. |
| `intake.podDisruptionBudget.maxUnavailable` | int | `1` | Maximum unavailable pods. |
| `querier.replicaCount` | int | `2` | Replicas. Ignored when `autoscaling.enabled`. |
| `querier.resources` | object | `{}` | Resource requests and limits. |
| `querier.service.type` | string | `"ClusterIP"` | Service type. |
| `querier.service.annotations` | object | `{}` | Service annotations. |
| `querier.autoscaling.enabled` | bool | `false` | Create a HorizontalPodAutoscaler. |
| `querier.autoscaling.minReplicas` | int | `2` | Minimum replicas. |
| `querier.autoscaling.maxReplicas` | int | `10` | Maximum replicas. |
| `querier.autoscaling.targetCPUUtilizationPercentage` | int | `70` | Target average CPU utilization (percent of the request). |
| `querier.autoscaling.targetMemoryUtilizationPercentage` | null | `null` | Target average memory utilization (percent of the request). |
| `querier.autoscaling.behavior` | object | `{}` | Scaling behavior, passed through to the HPA. |
| `querier.podDisruptionBudget.enabled` | bool | `false` | Create a PodDisruptionBudget. |
| `querier.podDisruptionBudget.minAvailable` | null | `null` | Minimum available pods. |
| `querier.podDisruptionBudget.maxUnavailable` | int | `1` | Maximum unavailable pods. |
| `compactor.replicaCount` | int | `1` | Replicas. More than one share the background work by data stream and organization, and the rest take over when a node fails. |
| `compactor.resources` | object | `{}` | Resource requests and limits. |
| `compactor.podDisruptionBudget.enabled` | bool | `false` | Create a PodDisruptionBudget. |
| `compactor.podDisruptionBudget.minAvailable` | null | `null` | Minimum available pods. |
| `compactor.podDisruptionBudget.maxUnavailable` | int | `1` | Maximum unavailable pods. |
| `alert.replicaCount` | int | `1` | Replicas. More than one share rules, reports and pipelines; single-point jobs run on the primary and move when it fails. |
| `alert.resources` | object | `{}` | Resource requests and limits. This role renders scheduled reports with a headless Chromium (included in the image), so give it memory. |
| `alert.service.type` | string | `"ClusterIP"` | Service type. Probe agents connect to ports 5084 (register) and 5085 (control); expose them with `LoadBalancer` or `NodePort`, and set the matching public URLs in `config.probe`. |
| `alert.service.annotations` | object | `{}` | Service annotations. |
| `alert.podDisruptionBudget.enabled` | bool | `false` | Create a PodDisruptionBudget. |
| `alert.podDisruptionBudget.minAvailable` | null | `null` | Minimum available pods. |
| `alert.podDisruptionBudget.maxUnavailable` | int | `1` | Maximum unavailable pods. |
| `web.enabled` | bool | `false` | Deploy the optional nginx layer in front of the router (static UI plus an `/api` proxy). Normally unnecessary: the UI is embedded in the application and served by the router. No web image is published yet, so you must build one from the application's `deploy/docker/Dockerfile.web` and set `web.image`. |
| `web.image.repository` | string | `""` | Web image. Required when `web.enabled`. |
| `web.image.tag` | string | `""` | Web image tag. Empty means the chart's `appVersion` (`latest`). |
| `web.image.digest` | string | `""` | Image digest. |
| `web.image.pullPolicy` | string | `"Always"` | Image pull policy. |
| `web.replicaCount` | int | `2` | Replicas. |
| `web.resources` | object | `{"requests":{"cpu":"50m","memory":"64Mi"},"limits":{"cpu":"500m","memory":"25...` | Resource requests and limits. |
| `web.service.type` | string | `"ClusterIP"` | Service type. |
| `web.service.port` | int | `80` | Service port. |
| `web.service.annotations` | object | `{}` | Service annotations. |
| `ingress.enabled` | bool | `false` | Expose the UI and HTTP API (router, or the standalone Service, or `web`). |
| `ingress.className` | string | `""` | Ingress class. |
| `ingress.annotations` | object | `{}` | Ingress annotations. With ingress-nginx, live tail needs `nginx.ingress.kubernetes.io/proxy-buffering: "off"` and long read/send timeouts; see examples/ingress-nginx.yaml. |
| `ingress.hosts` | list | `[{"host":"molesignal.local","paths":[{"path":"/","pathType":"Prefix"}]}]` | Hosts and paths. |
| `ingress.tls` | list | `[]` | TLS settings, as in the Kubernetes Ingress API. |
| `otlpIngress.enabled` | bool | `false` | Expose OTLP over gRPC (port 4317) through an Ingress. Needs a controller that speaks gRPC to the backend, and usually TLS. |
| `otlpIngress.className` | string | `""` | Ingress class. |
| `otlpIngress.annotations` | object | `{}` | Ingress annotations, for example `nginx.ingress.kubernetes.io/backend-protocol: GRPC`. |
| `otlpIngress.hosts` | list | `[{"host":"otlp.molesignal.local","paths":[{"path":"/","pathType":"Prefix"}]}]` | Hosts and paths. |
| `otlpIngress.tls` | list | `[]` | TLS settings, as in the Kubernetes Ingress API. |
| `waitForDependencies.enabled` | bool | `true` | Hold MoleSignal pods back until the bundled PostgreSQL and RustFS answer, instead of letting them crash-loop while those start. Only used when the bundled services are enabled. Runs in the application image. |
| `waitForDependencies.resources` | object | `{}` | Resource requests and limits of the init container. |
| `extraObjects` | list | `[]` | Additional Kubernetes manifests to deploy with the release. Each item is a complete object, and may use template expressions, for example a NetworkPolicy. |
<!-- values:end -->

## License

Apache License 2.0.
