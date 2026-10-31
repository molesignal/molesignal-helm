# Probe Agent Helm chart

A [MoleSignal](https://www.molesignal.com) server runs synthetic checks from its own network. A
Probe Agent runs them from somewhere else: a private network, a region, an edge site. This chart
runs Agents on Kubernetes. They only open outbound connections, so nothing needs to reach them.

The chart does not deploy the server. That is the
[`molesignal`](https://github.com/molesignal/molesignal-helm/tree/main/charts/molesignal) chart, in this
cluster or anywhere else. Each chart has its own version (see
[versioning](https://github.com/molesignal/molesignal-helm/blob/main/docs/versioning.md)).

## Install

### 1. Give the server an address the Agents can reach

An Agent registers on port 5084 of a node running `standalone` or `alert`, and then keeps a control
stream open on port 5085. It trusts the server's own CA, so the host it connects to has to be on the
server's certificate, and the server tells it where to find the control port. All of that is
`[probe]` in the server's configuration, which by default only names `localhost`.

For Agents in the same cluster, in the values of the `molesignal` chart (here the release
`molesignal`, namespace `molesignal`, `standalone` mode; in `distributed` mode the Service is
`molesignal-alert`):

```yaml
config:
  probe:
    register_endpoint: https://molesignal.molesignal.svc.cluster.local:5084
    control_endpoint: https://molesignal.molesignal.svc.cluster.local:5085
    server_names: [molesignal.molesignal.svc.cluster.local]
```

For Agents elsewhere, expose the two ports with a `LoadBalancer` or `NodePort` Service and use that
name. **Nothing in between may terminate TLS**: the Agent only accepts a certificate from the
server's CA, and the control stream authenticates it with a client certificate. An ingress has to
pass TLS through, and is no help otherwise.

### 2. Create a location and a token

In MoleSignal open **Synthetics → Locations**, choose **Add private location**, and then either:

- **Register agent**, for a one-time token, or
- **Synthetics → Agents → Create Agent Token**, for a token that more than one Agent can use.

| | One-time token | Agent Token |
| --- | --- | --- |
| Registers | one Agent | any number of Agents, until it expires or is disabled |
| Valid for | 5 to 60 minutes, 15 by default | 30 days to a year, or for good |
| Use it for | `replicaCount: 1` | more than one replica, or a Secret you keep for adding replicas later |

Both are shown once. Both come with the register command, whose `--endpoint` and
`--ca-certificate-base64` you need in the next step.

### 3. Install

```bash
kubectl create namespace probes
kubectl --namespace probes create secret generic probe-agent-registration \
  --from-literal=token=<the token>

helm repo add molesignal https://charts.molesignal.com
helm repo update

helm install probe-agent molesignal/probe-agent --namespace probes \
  --set registration.endpoint=<--endpoint> \
  --set registration.caCertificateBase64=<--ca-certificate-base64> \
  --set registration.existingSecret=probe-agent-registration
```

or from the OCI registry, `oci://ghcr.io/molesignal/charts/probe-agent --version 0.1.0`.

`helm test probe-agent --namespace probes` checks the endpoint and the CA by trying to register with
a token the server has to refuse. The Agents then appear in **Synthetics → Agents**. Their location
is healthy when all of them are online; checks run once you select that location.

[`examples/probe-agent/agent-pool.yaml`](https://github.com/molesignal/molesignal-helm/blob/main/examples/probe-agent/agent-pool.yaml)
shows a larger setup. `registration.token` takes the token directly, in which case the chart makes
the Secret; the value then also stays in the release's history, so prefer a Secret of your own.

## How an Agent keeps its identity

The chart runs a StatefulSet because an Agent *is* its identity: a key it generates itself and a
certificate the server signs. Those, and the results that wait while the server cannot be reached,
live on a volume of 1 GiB per replica.

- An init container, `register`, runs before the Agent. It registers when the volume has no identity
  yet, and does nothing when it has. So a pod that is replaced, moved or restarted stays the same
  Agent, and a one-time token that has been used is not a problem.
- **The token is only read until then.** A new token restarts nothing, and you can delete the Secret
  once the Agents are online. A replica that has no identity and no token waits in `Init` and says so.
- **Each replica registers on its own**, as its own Agent in the location, named after its pod.
- **Scaling down keeps the volume and the Agent**, which comes back with its identity when you scale
  up. To retire one, revoke it in MoleSignal and delete its PVC, `state-<release>-probe-agent-<n>`.
  `persistentVolumeClaimRetentionPolicy` can delete the PVCs for you, but the Agent stays behind in
  MoleSignal as offline until you revoke it.
- **Uninstalling keeps the volumes too**, so installing the same release again brings back the same
  Agents. Deleting the Secret is up to you if it is your own.
- `persistence.enabled: false` keeps the identity in an `emptyDir`. Every new pod is then a new Agent
  and the old one stays behind as offline. That needs an Agent Token, and is meant for throw-away
  clusters.

The identity is in a subdirectory of the volume, `/var/lib/molesignal-probe/state`, because
`probe-agent register` makes its directory private with `chmod 0700`, which a process that runs as
10001 may not do to the root of a volume.

The certificate is valid for 30 days (`[probe].certificate_days` on the server) and the Agent asks
for a new one when it connects with less than a week left. An Agent that stays away for longer than
that cannot come back: revoke it, delete its PVC, delete the pod, and it registers again.

## Browser checks

The image carries Chromium, and an Agent starts one per browser check, up to `browser.maxConcurrent`
at a time. Set that to 0 and the Agent stops offering browser checks, and needs far less memory.

**Sandbox.** Chromium's sandbox needs unprivileged user namespaces, and `chrome-sandbox`, the fallback
in the image, needs `CAP_SYS_ADMIN`. The `RuntimeDefault` seccomp profile rules out the first and the
chart's security context the second, so by default the chart starts Chromium with `--no-sandbox`. The
pod is what contains it then: not root, no capabilities, no service account token, a read-only root.
A page that exploits Chromium has the rights of that pod, including the Agent's key on its volume.

To run with the sandbox, set `browser.sandbox: true` and
`securityContext.seccompProfile: {type: Unconfined}`. The nodes then have to allow unprivileged user
namespaces; on Ubuntu 23.10 and later that takes `sysctl kernel.apparmor_restrict_unprivileged_userns=0`.
The `baseline` and `restricted` Pod Security Standards do not allow `Unconfined`.

**`/dev/shm`.** Nothing to do. The Agent starts Chromium with `--disable-dev-shm-usage`, so the small
default `/dev/shm` of a pod stays unused and shared memory goes to `/tmp` instead, an `emptyDir` next
to the profile. `browser.tmp` configures it: `{medium: Memory}` keeps it in memory, where it counts
towards the memory limit, and going over `sizeLimit` evicts the pod.

**Sizing.** The defaults (512 MiB requested, a limit of 2 GiB, no CPU limit) are for one browser check
at a time. The Agent caps the JavaScript heap of a page at 512 MiB, and a page uses more than that
in total. Add at least 1 GiB of limit and half a CPU per `browser.maxConcurrent`, and watch the real
pages you check. Going over the limit kills a Chromium process first, and that check fails.

## Other things to know

- **Pings.** ICMP checks, and the traceroute after a failed check, need a raw socket or an ICMP
  socket. The chart sets `net.ipv4.ping_group_range` for the pod instead of adding `NET_RAW`.
- **Proxies.** HTTP checks honour `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY`; set them in
  `extraEnv`. Through a proxy, the egress policy of the location no longer applies to the target.
- **Egress policy.** A location refuses private and loopback addresses unless you allow them. Agents in
  a cluster often check things in it: tick **Allow private networks** on the location.
- **Images.** The image follows `latest` and is pulled on every pod start, like the application's in
  the `molesignal` chart. `global.imageRegistry` applies:
  `ghcr.io/molesignal/probe-agent` becomes `<registry>/molesignal/probe-agent`. Pin `image.tag` (or
  `image.digest`) and go back to `pullPolicy: IfNotPresent` for anything beyond trying the chart out.
- **Versions.** The chart needs application 26.2.0.0 or later on the server. An Agent older than the
  server's features only gets the checks it can run; see the synthetics documentation.
- **Security context.** The defaults pass the `restricted` Pod Security Standard, sysctl included.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| A pod stays in `Init` | `kubectl logs <pod> -c register`. See below. |
| `connect to Probe endpoint` | The endpoint cannot be reached, or its certificate does not name its host: check `[probe].server_names` on the server and `registration.endpoint`. |
| `invalid, expired, disabled, or already-used Probe credential` | The token: a one-time token that has been used or has expired, or one being shared by replicas. Put a new one in the Secret; the pod picks it up at its next retry. |
| `no identity ... and no token to register with` | The Secret is gone, or does not have the key `registration.existingSecretKey`. |
| Registered, but never online | The control endpoint comes from the server's `[probe].control_endpoint`, not from these values. `kubectl logs <pod>` says what the connection does. To change it, fix it on the server, delete the PVC and register again: the Agent keeps the address it got. |
| Browser checks fail with `launch Chrome` | You set `browser.sandbox: true` on nodes or under a profile that do not allow it. See [Browser checks](#browser-checks). |
| `helm test` fails | Its log has the error the Agent would get. It needs nothing but the endpoint and the CA. |
| `helm install` fails with `probe-agent:` | A guard refused the values; the message says what to change. |

## Values

The table is generated from `values.yaml` (`python3 scripts/gen-values-doc.py`).

<!-- values:start -->
| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `global.imageRegistry` | string | `""` | Replace the registry host of the image (mirror or air-gapped registry). `ghcr.io/molesignal/probe-agent` becomes `<imageRegistry>/molesignal/probe-agent`. |
| `global.imagePullSecrets` | list | `[]` | Pull secrets for every pod the chart creates. Merged with `imagePullSecrets`. |
| `nameOverride` | string | `""` | Override the chart name used in resource names and labels. |
| `fullnameOverride` | string | `""` | Override the prefix of every resource name (default: the release name). Kept to 43 characters. |
| `commonLabels` | object | `{}` | Labels added to every resource. |
| `commonAnnotations` | object | `{}` | Annotations added to every resource. |
| `image.repository` | string | `"ghcr.io/molesignal/probe-agent"` | Agent image. It carries Chromium for browser checks. |
| `image.tag` | string | `""` | Image tag. Empty means the chart's `appVersion`, which is `latest`. That tag moves: pin a version (or `digest`) for anything but trying the chart out. |
| `image.digest` | string | `""` | Image digest (`sha256:...`). When set it is used together with the tag. |
| `image.pullPolicy` | string | `"Always"` | Image pull policy. `Always`, because the default tag moves; use `IfNotPresent` once you pin one. |
| `imagePullSecrets` | list | `[]` | Pull secrets, as a list of `{name: ...}`. |
| `replicaCount` | int | `1` | Replicas. Each one is an Agent of its own in the location, with its own identity, and each one registers, so more than one needs an Agent Token: a one-time token registers a single Agent. |
| `registration.endpoint` | string | `""` | Address of the server's register port, for example `https://molesignal.example.com:5084`. Required. The host has to be one of the `[probe].server_names` of the server, and the port reachable at layer 4: the Agent checks the server's certificate against the CA below and shows a client certificate later on, so nothing in between may terminate TLS. |
| `registration.caCertificateBase64` | string | `""` | Base64 of the server's Probe CA, the `--ca-certificate-base64` of the register command. Required. Not a secret: it only tells the Agent which server to trust. |
| `registration.token` | string | `""` | Token to register with, stored in a Secret the chart creates. Prefer `existingSecret`: a value given here also ends up in the release's history. A one-time token registers one Agent and expires within the hour. An Agent Token (`ms_probe_...`) registers any number until it expires or is disabled. |
| `registration.existingSecret` | string | `""` | Existing Secret that holds the token. An Agent only reads it until it has an identity, so you can delete the Secret afterwards; a replica without an identity then stays in Init and says so in its log. |
| `registration.existingSecretKey` | string | `"token"` | Key of the token inside that Secret (the Secret the chart creates uses it too). |
| `registration.resources` | object | `{}` | Resource requests and limits of the init container that registers. |
| `maxConcurrent` | int | `4` | Checks one Agent runs at the same time (`--max-concurrent`). The Agent caps it at 32. |
| `browser.maxConcurrent` | int | `1` | Browser checks one Agent runs at the same time (`--max-browser-concurrent`), each with a Chromium of its own and within `maxConcurrent`. The Agent caps it at 4. 0 stops the Agent from offering browser checks at all. |
| `browser.sandbox` | bool | `false` | Run Chromium with its own sandbox. Off by default because the sandbox needs user namespaces, which the `RuntimeDefault` seccomp profile below blocks; `--no-sandbox` is what works under the `restricted` Pod Security Standard. A page that breaks out of a renderer then has this pod's rights, including its Agent key. To switch it on, also set `seccompProfile: {type: Unconfined}` in `securityContext`, on nodes whose kernel allows unprivileged user namespaces. |
| `browser.tmp` | object | `{}` | `emptyDir` settings of `/tmp`, where Chromium keeps its profile and its shared memory (the Agent starts it with `--disable-dev-shm-usage`, so `/dev/shm` stays unused). For example `{medium: Memory, sizeLimit: 1Gi}`; memory counts towards the limit in `resources`, and going over `sizeLimit` evicts the pod. |
| `logLevel` | string | `"probe_agent=info"` | Log filter of the Agent, in `tracing` env-filter syntax (`RUST_LOG`). |
| `persistence.enabled` | bool | `true` | Keep each Agent's identity (its key and certificate) and its unsent results on a PersistentVolumeClaim, so that a new pod is the same Agent. When false an `emptyDir` is used: every new pod registers as a new Agent and the old one stays behind as offline. |
| `persistence.size` | string | `"1Gi"` | Volume size per replica. The identity takes a few kilobytes; the rest is for results that wait while the server cannot be reached. |
| `persistence.storageClass` | string | `""` | Storage class. Empty uses the cluster default. |
| `persistence.accessModes` | list | `["ReadWriteOnce"]` | Access modes. |
| `persistence.annotations` | object | `{}` | Annotations of the PVCs. |
| `persistentVolumeClaimRetentionPolicy` | object | `{}` | What happens to the PVCs when the StatefulSet is deleted or scaled down (`whenDeleted` / `whenScaled`: `Retain` or `Delete`). Empty keeps the cluster default, `Retain`, which lets an Agent come back with its identity. |
| `resources` | object | `{"requests":{"cpu":"200m","memory":"512Mi"},"limits":{"memory":"2Gi"}}` | Resource requests and limits of the Agent. Most of it is for Chromium: size the memory for `browser.maxConcurrent` pages (see the README), or lower it when you set that to 0. |
| `podSecurityContext` | object | `{"fsGroup":10001,"fsGroupChangePolicy":"OnRootMismatch","sysctls":[{"name":"n...` | Pod security context. The image runs as uid/gid 10001. `ping_group_range` lets the Agent send pings (ICMP checks, and traceroute after a failure) without `NET_RAW`. |
| `securityContext` | object | `{"runAsNonRoot":true,"runAsUser":10001,"runAsGroup":10001,"allowPrivilegeEsca...` | Container security context, for the Agent and the init container. Passes the `restricted` Pod Security Standard. |
| `serviceAccount.create` | bool | `true` | Create a service account. |
| `serviceAccount.name` | string | `""` | Name of the service account. Defaults to the full name. |
| `serviceAccount.annotations` | object | `{}` | Annotations for the service account. |
| `serviceAccount.automount` | bool | `false` | Mount the service account token into the pods. The Agent does not call the Kubernetes API, so it is off by default. |
| `extraEnv` | list | `[]` | Extra environment variables for the Agent and the init container, for example `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`, which HTTP checks honour. |
| `extraEnvFrom` | list | `[]` | Extra `envFrom` sources for the Agent and the init container. |
| `extraVolumes` | list | `[]` | Extra volumes for the pods. |
| `extraVolumeMounts` | list | `[]` | Extra volume mounts for the Agent and the init container. |
| `podAnnotations` | object | `{}` | Annotations added to the pods. |
| `podLabels` | object | `{}` | Labels added to the pods. |
| `nodeSelector` | object | `{}` | Node selector. A location is often a place: use it to keep the Agents there. |
| `tolerations` | list | `[]` | Tolerations. |
| `affinity` | object | `{}` | Affinity. |
| `topologySpreadConstraints` | list | `[]` | Topology spread constraints. The chart does not add the label selector for you; use `app.kubernetes.io/instance` and `app.kubernetes.io/component: agent`. |
| `priorityClassName` | string | `""` | Priority class. |
| `extraObjects` | list | `[]` | Additional Kubernetes manifests to deploy with the release. Each item is a complete object, and may use template expressions, for example a NetworkPolicy. |
<!-- values:end -->

## License

Apache License 2.0.
