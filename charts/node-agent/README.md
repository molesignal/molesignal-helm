# Node agent Helm chart

[`molesignal-node-agent`](https://github.com/molesignal/molesignal-node-agent) observes Linux
nodes: it collects kernel events, host metrics and profiles with eBPF and sends them over OTLP to a
[MoleSignal](https://www.molesignal.com) intake. This chart runs it as a DaemonSet, one agent per
node, in **standalone token mode**: an endpoint and a bearer token, nothing else.

The chart is the Helm form of
[`deploy/kubernetes/node-agent.yaml`](https://github.com/molesignal/molesignal-node-agent/tree/main/deploy/kubernetes)
in the agent's repository, whose README explains every host namespace, mount and capability; so do
the comments in [`values.yaml`](values.yaml) and the tables below. It does not deploy the server:
that is the [`molesignal`](https://github.com/molesignal/molesignal-helm/tree/main/charts/molesignal)
chart, in this cluster or anywhere else. Each chart has its own version (see
[versioning](https://github.com/molesignal/molesignal-helm/blob/main/docs/versioning.md)).

> **Status.** Version 0.1.0 of the chart deploys `molesignal/molesignal-node-agent:0.1.0`, a
> multi-architecture image for linux/amd64 (x86_64) and linux/arm64 (aarch64). That image is not
> published yet, and neither the chart nor the manifest it mirrors has been installed on a real
> cluster: they render and pass `helm lint` and the schema validation, which prove little about
> BPF, host paths and Pod Security on real nodes.

## Install

### 1. A namespace the agent may run in

The agent needs host namespaces, host paths and added capabilities, so its namespace has to allow
the `privileged` [Pod Security level](https://kubernetes.io/docs/concepts/security/pod-security-standards/):
`baseline` already refuses all three.

```bash
kubectl create namespace molesignal
kubectl label namespace molesignal pod-security.kubernetes.io/enforce=privileged
```

Or let the chart create the namespace with that label: `--set namespace.create=true`, without
`--create-namespace`, and only for a namespace that does not exist yet. It is kept on uninstall.

### 2. A token

```bash
kubectl --namespace molesignal create secret generic molesignal-node-agent-token \
  --from-file=token=./agent.token
```

`token.value` takes the token directly, in which case the chart makes the Secret; the value then also
stays in the release's history, so prefer a Secret of your own. The agent re-reads the file while
it runs, so a new token in the Secret reaches it without a restart (the kubelet takes up to a
minute or so to update the file).

### 3. Install

```bash
helm repo add molesignal https://charts.molesignal.com
helm repo update

helm install molesignal molesignal/node-agent --namespace molesignal \
  --set endpoint=https://intake.molesignal.example \
  --set token.existingSecret=molesignal-node-agent-token
```

or from the OCI registry, `oci://ghcr.io/molesignal/charts/node-agent --version 0.1.0`.

With the release called `molesignal`, every object is called `molesignal-node-agent`, as in the
agent's manifest. Then ask one of the agents what works on its node:

```bash
kubectl --namespace molesignal exec daemonset/molesignal-node-agent -- molesignal-node-agent doctor
```

[`examples/node-agent/fewer-host-mounts.yaml`](https://github.com/molesignal/molesignal-helm/blob/main/examples/node-agent/fewer-host-mounts.yaml)
shows a setup without the Docker socket and debugfs, with its own PriorityClass and some agent
settings.

## Key values

| Value | Default | What it does |
| --- | --- | --- |
| `endpoint` | — (required) | `MS_ENDPOINT`, the OTLP intake, `http://` or `https://` |
| `token.existingSecret` / `token.value` | — (one is required) | the bearer token, mounted as `/etc/molesignal-node-agent/token` with mode 0400 |
| `config.*` | see below | the agent's `MS_*` settings, written into a ConfigMap; a change rolls the pods |
| `hostMounts.<name>.enabled` | `true` | switch an optional host mount off |
| `state.hostPath` | `/var/lib/molesignal-node-agent` | the node's directory for the spool |
| `securityContext` | not privileged, 9 capabilities | see [Capabilities](#capabilities) |
| `priorityClassName` / `priorityClass.create` | `system-node-critical` / `false` | the pods' priority, or a PriorityClass of the release's own |
| `namespace.create` | `false` | create the namespace with the Pod Security label |
| `tolerations` | every taint | so that tainted nodes are observed too |
| `nodeSelector` | `kubernetes.io/os: linux` | the agent is Linux-only |
| `updateStrategy` | `RollingUpdate`, `maxUnavailable: 1` | one node at a time |
| `resources` | 50m / 96Mi requested, 512Mi limit | |
| `enrollment.managed` | `false` | reserved, see [Managed enrollment](#managed-enrollment) |
| `extraEnv`, `extraEnvFrom`, `extraVolumes`, `extraVolumeMounts` | empty | anything else |

### The agent's settings

The agent has no configuration file in standalone mode; it reads `MS_*` environment variables, and
refuses to start when one is wrong (`molesignal-node-agent validate-config` prints what it read).
The chart writes them into a ConfigMap from `config`. A `null` leaves a variable out, so the agent's
own default applies; an empty string is kept, because for some variables it means something.

| Value | Variable | Default here | Notes |
| --- | --- | --- | --- |
| `endpoint` | `MS_ENDPOINT` | — | required |
| `config.logLevel` | `MS_LOG_LEVEL` | `null` (agent: `info`) | `trace` … `error` |
| `config.logFormat` | `MS_LOG_FORMAT` | `json` | `text` or `json` |
| `config.healthAddr` | `MS_HEALTH_ADDR` | `127.0.0.1:9102` | loopback only; with `hostNetwork` the port must be free on every node |
| `config.shutdownTimeoutMs` | `MS_SHUTDOWN_TIMEOUT_MS` | `null` (agent: 10000) | must stay below `terminationGracePeriodSeconds` |
| `config.collectors.allow` / `.deny` | `MS_COLLECTORS_ALLOW` / `_DENY` | empty | comma-joined; a name cannot be in both |
| `config.dropwatchFilter` | `MS_DROPWATCH_FILTER` | `tcp` | tcpdump subset; `""` reports every drop |
| `config.netdev.eventsDevices` | `MS_NETDEV_EVENTS_DEVICES` | `null` | full-match regexes; `null` every interface, `[]` none |
| `config.netdev.hwDevices` | `MS_NETDEV_HW_DEVICES` | `null` | the same |
| `config.netdev.dcbDevices` | `MS_NETDEV_DCB_DEVICES` | `null` | the same |
| `config.legacyMetrics` | `MS_COMPATIBILITY_LEGACY_METRICS` | `null` (agent: off) | also send the old `huatuo_bamai_*` names |
| `config.extra` | any other `MS_*` | empty | spool, retry, batching, stream names; never a secret |
| (fixed) | `MS_TOKEN_PATH` | `/etc/molesignal-node-agent/token` | follows the token mount |
| (fixed) | `MS_STATE_DIR` | `/var/lib/molesignal-node-agent` | follows the state mount |

## What the agent gets from the node

### Host namespaces

Fixed, not values: the agent does not work without them.

| Setting | Why |
| --- | --- |
| `hostPID: true` | reads every process's `/proc/<pid>` (cgroup, netns, D-state stacks, top RSS); BPF events carry host pids |
| `hostNetwork: true` | interface, qdisc and netstat metrics belong to the node's network namespace; the health endpoint listens on loopback, and the probes reach it through `exec` |
| `dnsPolicy: ClusterFirstWithHostNet` | cluster names still resolve, for an intake inside the cluster |

The kubelet's read-only port (`127.0.0.1:10255`), reached through the node's network, gives pod names
and namespaces. Where it is off those fields are missing, and `doctor` warns.

### Host mounts

Every mount is read-only except the state directory. Each has a fixed place in the container and a
`hostPath` you can change; all but `machineId` can be switched off with `enabled: false`.

| Value | In the container | For | Without it |
| --- | --- | --- | --- |
| `hostMounts.machineId` | `/etc/machine-id` | the node's identity (`host.id`) | **required**: every node would report as the same host |
| `hostMounts.osRelease` | `/etc/os-release` | the node's distribution (`os.version`) | the image's distribution is reported |
| `hostMounts.btf` | `/sys/kernel/btf` | CO-RE BPF (oom, dropwatch, tcp_retransmit, …) | those collectors are degraded |
| `hostMounts.tracefs` | `/sys/kernel/tracing` | tracepoint ids for libbpf | tracepoint collectors are degraded, unless debugfs is there |
| `hostMounts.debugfs` | `/sys/kernel/debug` | the same, on kernels that only have the debugfs path | nothing, where tracefs is mounted |
| `hostMounts.cgroup` | `/sys/fs/cgroup` | container discovery, cgroup metrics | no container dimension |
| `hostMounts.kmsg` | `/dev/kmsg` | SysRq stack output for hung-task and soft-lockup events | the events go out with an error text for the stacks |
| `hostMounts.dockerSocket` | `/var/run/docker.sock` | container names on Docker nodes | names missing on Docker nodes; nothing changes on containerd nodes |
| `state.hostPath` | `/var/lib/molesignal-node-agent` (read-write) | the spool of undelivered batches, AutoTracing cool-downs | every new pod loses what the old one had not sent |
| Secret | `/etc/molesignal-node-agent/token` (0400) | the bearer token | the agent does not start |

### Capabilities

Not privileged: `drop: [ALL]`, and only these come back.

| Capability | For |
| --- | --- |
| `BPF`, `PERFMON` | loading and attaching BPF programs, perf buffers (kernel 5.8+) |
| `SYS_ADMIN` | BPF below kernel 5.8; `setns` for netns cookies; `/proc/<pid>/stack` |
| `SYS_RESOURCE` | the BPF memlock limit of older kernels |
| `SYS_PTRACE`, `DAC_READ_SEARCH` | reading other processes' `/proc` entries |
| `NET_ADMIN` | rtnetlink, devlink and qdisc queries |
| `SYSLOG` | reading `/dev/kmsg` when `kernel.dmesg_restrict=1` |
| `KILL` | ending the process groups of diagnostic tools the agent runs |

It runs as root on a read-only root filesystem, with `seccompProfile: Unconfined`, because the default
seccomp profile of some runtimes blocks `bpf()` and `perf_event_open()`. `securityContext` is one value:
setting it replaces the whole default, and `capabilities.add` has to list every capability you keep.

- **SELinux** (enforcing): host paths and BPF need `spc_t`; add `seLinuxOptions: {type: spc_t}`.
- **AppArmor**: `runtime/default` may block `/sys/kernel/debug` and BPF; add
  `appArmorProfile: {type: Unconfined}` (Kubernetes 1.30+).
- **SysRq stacks**: runtimes mount `/proc/sysrq-trigger` read-only, so hung-task and soft-lockup
  stacks of every CPU are missing (the events still go out). Only `privileged: true` changes that.
- **`fsGroup`** is refused: with it the kubelet makes the token group-readable, which the agent does
  not accept.

## Other things to know

- **Updates.** One node at a time (`maxUnavailable: 1`). After `SIGTERM` the agent spends up to
  `MS_SHUTDOWN_TIMEOUT_MS` (10 s) sending what it has; the rest stays in the node's state directory
  and the next pod sends it. `terminationGracePeriodSeconds` (30) has to be longer, and the chart
  refuses values where it is not.
- **Priority.** `system-node-critical` keeps the agent on a node under pressure. Some managed clusters
  only admit the `system-*` classes in namespaces with a ResourceQuota that names them: create such a
  quota, set `priorityClass.create: true` for a class of the release's own, or set
  `priorityClassName: ""`.
- **Two releases on the same nodes** need different `state.hostPath` and `config.healthAddr` ports:
  both are the node's. A created PriorityClass is cluster-wide and named after the release.
- **Coming from the manifest.** The DaemonSet's selector is the chart's (`app.kubernetes.io/name:
  node-agent`, `instance`, `component`), which Kubernetes does not let change in place, and Helm does
  not adopt objects it did not create: delete the objects of `node-agent.yaml` (keep the token Secret)
  before installing. The state directory on the nodes is the same, so the new pods send what the old
  ones left.
- **Images.** `global.imageRegistry` applies: `molesignal/molesignal-node-agent` becomes
  `<registry>/molesignal/molesignal-node-agent`. The default tag is the chart's `appVersion`, a fixed
  release, pulled `IfNotPresent`.

## Managed enrollment

The agent is meant to get an mTLS identity and its configuration from the MoleSignal control plane
one day. That protocol does not exist yet, so the agent only runs in standalone token mode. The chart
keeps `enrollment.managed` for it, off by default; setting it to `true` is refused until a chart
version supports it.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| No pods, and the DaemonSet's events say `violates PodSecurity` | The namespace is not `privileged`: see step 1. |
| A pod stays in `ContainerCreating`, `secret ... not found` | The token Secret, or its key `token.existingSecretKey`, does not exist. |
| The pod exits at once, `export.token_path: must be readable by owner only` | Something made the token group-readable: an `fsGroup`, or a mutating webhook that changes the pod. |
| The pod exits at once with another `MS_*` or `agent.` / `export.` message | A setting the agent refuses; `kubectl logs` names it. `validate-config` prints what it read. |
| `hostPath type check failed: /etc/machine-id` (or `/dev/kmsg`, `/sys/fs/cgroup`) | The node keeps it elsewhere: set `hostMounts.<name>.hostPath`, or switch the mount off where that is allowed. |
| Collectors reported as degraded | `doctor` says why: a mount switched off, no BTF, a kernel without the hook, or an LSM in the way (see [Capabilities](#capabilities)). |
| No pod names or namespaces on the data | The kubelet's read-only port is off on that node. |
| Not scheduled because of the priority class | See **Priority** above. |
| `helm install` fails with `node-agent:` | A guard refused the values; the message says what to change. |

## Values

The table is generated from `values.yaml` (`python3 scripts/gen-values-doc.py`).

<!-- values:start -->
| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `global.imageRegistry` | string | `""` | Replace the registry host of the image (mirror or air-gapped registry). `molesignal/molesignal-node-agent` becomes `<imageRegistry>/molesignal/molesignal-node-agent`. |
| `global.imagePullSecrets` | list | `[]` | Pull secrets for every pod the chart creates. Merged with `imagePullSecrets`. |
| `nameOverride` | string | `""` | Override the chart name used in resource names and labels. |
| `fullnameOverride` | string | `""` | Override the prefix of every resource name (default: the release name, followed by the chart name unless the release name contains it). Kept to 52 characters. |
| `commonLabels` | object | `{}` | Labels added to every resource. They never reach the DaemonSet's selector. |
| `commonAnnotations` | object | `{}` | Annotations added to every resource. |
| `image.repository` | string | `"molesignal/molesignal-node-agent"` | Agent image, published for linux/amd64 (x86_64) and linux/arm64 (aarch64) under one multi-architecture tag. |
| `image.tag` | string | `""` | Image tag. Empty means the chart's `appVersion`. |
| `image.digest` | string | `""` | Image digest (`sha256:...`). When set it is used together with the tag. |
| `image.pullPolicy` | string | `"IfNotPresent"` | Image pull policy. The default tag is a fixed release, so pulling once is enough. |
| `imagePullSecrets` | list | `[]` | Pull secrets, as a list of `{name: ...}`. |
| `endpoint` | string | `""` | OTLP intake the agent sends to (`MS_ENDPOINT`), `http://` or `https://`, for example `https://intake.molesignal.example`. Required. With `hostNetwork` the pod still resolves cluster names (`dnsPolicy: ClusterFirstWithHostNet`), so an intake Service in this cluster works too. |
| `token.value` | string | `""` | Bearer token, stored in a Secret the chart creates. Prefer `existingSecret`: a value given here also ends up in the release's history. Required, unless `existingSecret` is set. |
| `token.existingSecret` | string | `""` | Existing Secret that holds the token, for example one made with `kubectl create secret generic molesignal-node-agent-token --from-file=token=./agent.token`. The agent re-reads the file while it runs, so a new token in the Secret is picked up without a restart. |
| `token.existingSecretKey` | string | `"token"` | Key of the token inside that Secret (the Secret the chart creates uses it too). It is mounted as `/etc/molesignal-node-agent/token`, readable by its owner only (0400): the agent refuses a token file that its group or others can read. |
| `enrollment.managed` | bool | `false` | Reserved for managed enrollment (an mTLS identity and configuration pushed from the control plane). The control protocol does not exist yet, so the agent only supports standalone token mode and the chart refuses `true`. Off by default, and nothing else changes when it is. |
| `config.logLevel` | null | `null` | `MS_LOG_LEVEL`: `trace`, `debug`, `info`, `warn` or `error`. `null` is the agent's default, `info`. |
| `config.logFormat` | string | `"json"` | `MS_LOG_FORMAT`: `text` or `json`. |
| `config.healthAddr` | string | `"127.0.0.1:9102"` | `MS_HEALTH_ADDR`: where `/healthz` and `/readyz` listen. Loopback only (`127.0.0.1`, `localhost` or `::1`, and a port). With `hostNetwork` that is the node's loopback: the port must be free on every node, and the probes reach it through `exec`, from the same network namespace. |
| `config.shutdownTimeoutMs` | null | `null` | `MS_SHUTDOWN_TIMEOUT_MS`: how long the agent spends sending what is left after `SIGTERM`. `null` is the agent's default, 10000. Must stay below `terminationGracePeriodSeconds`; what is not sent stays in the state directory and goes out after the next start. |
| `config.collectors.allow` | list | `[]` | `MS_COLLECTORS_ALLOW`: collectors to run, instead of all the default ones. Empty runs the defaults. `molesignal-node-agent capabilities` lists them. |
| `config.collectors.deny` | list | `[]` | `MS_COLLECTORS_DENY`: collectors not to run. A name cannot be in both lists. |
| `config.dropwatchFilter` | string | `"tcp"` | `MS_DROPWATCH_FILTER`: which dropped packets the dropwatch collector reports, in a subset of tcpdump syntax (`tcp and port 443`, `not arp`; no byte offsets like `tcp[13]`). `""` reports every drop; `null` is the agent's default, `tcp`. |
| `config.netdev.eventsDevices` | null | `null` | `MS_NETDEV_EVENTS_DEVICES`: network interfaces the netdev_events collector watches, each a regular expression that has to match the whole name (`eth0`, `en.*`), without commas. `null` watches every interface; `[]` watches none. |
| `config.netdev.hwDevices` | null | `null` | `MS_NETDEV_HW_DEVICES`: the same for netdev_hw. |
| `config.netdev.dcbDevices` | null | `null` | `MS_NETDEV_DCB_DEVICES`: the same for netdev_dcb. |
| `config.legacyMetrics` | null | `null` | `MS_COMPATIBILITY_LEGACY_METRICS`: also send every metric under its old `huatuo_bamai_*` name, for dashboards that still use them. A transition aid that the agent will remove (see `MS_COMPATIBILITY_LEGACY_METRICS_REMOVED_AT`). `null` is the agent's default, off. |
| `config.extra` | object | `{}` | Any other `MS_*` variable, as `NAME: "value"`, for example `MS_SPOOL_QUOTA_BYTES: "2147483648"`, `MS_EXPORT_TIMEOUT_MS`, `MS_BATCH_MAX_RECORDS`, `MS_RETRY_*`, `MS_STREAM_NAME`. The agent's README lists them. Never a secret: this is a ConfigMap. The variables the chart sets itself are refused here. |
| `hostMounts.machineId` | object | `{"hostPath":"/etc/machine-id"}` | `/etc/machine-id`, read-only: the node's identity (`host.id`). Required, and cannot be switched off: without it every pod reads the machine-id of the image, and all nodes report as the same host. |
| `hostMounts.osRelease` | object | `{"enabled":true,"hostPath":"/etc/os-release"}` | `/etc/os-release`, read-only: the node's distribution (`os.version`). Off, the agent reports the image's distribution instead. |
| `hostMounts.btf` | object | `{"enabled":true,"hostPath":"/sys/kernel/btf"}` | `/sys/kernel/btf`, read-only: kernel type information for the CO-RE BPF programs (oom, dropwatch, tcp_retransmit and others). Off, those collectors are degraded. |
| `hostMounts.tracefs` | object | `{"enabled":true,"hostPath":"/sys/kernel/tracing"}` | `/sys/kernel/tracing`, read-only: where libbpf looks up tracepoint ids. Off (and without `debugfs`), tracepoint collectors are degraded. |
| `hostMounts.debugfs` | object | `{"enabled":true,"hostPath":"/sys/kernel/debug"}` | `/sys/kernel/debug`, read-only: the same through debugfs, which is the only path on some kernels. Can go where `/sys/kernel/tracing` exists, or where the node's AppArmor profile blocks it anyway. |
| `hostMounts.cgroup` | object | `{"enabled":true,"hostPath":"/sys/fs/cgroup"}` | `/sys/fs/cgroup`, read-only: container discovery, cgroup metrics, `memory.current`. Off, nothing has a container dimension. |
| `hostMounts.kmsg` | object | `{"enabled":true,"hostPath":"/dev/kmsg"}` | `/dev/kmsg`, read-only: the kernel's SysRq stack output for hung-task and soft-lockup events. Off, those events still go out, with an error text for the stacks. |
| `hostMounts.dockerSocket` | object | `{"enabled":true,"hostPath":"/var/run/docker.sock"}` | `/var/run/docker.sock`, read-only: container names on Docker nodes. Harmless where it does not exist (containerd nodes); switch it off to keep the socket out of the pod. Off, container names are missing on Docker nodes. |
| `state.hostPath` | string | `"/var/lib/molesignal-node-agent"` | Directory of the node for the agent's state (`MS_STATE_DIR`, mounted read-write at `/var/lib/molesignal-node-agent`): the write-ahead spool of batches not yet delivered and the cool-down state of AutoTracing, so that a new pod sends what the old one could not. Created when missing. Give a second release on the same nodes a directory of its own. |
| `securityContext` | object | `{"privileged":false,"runAsUser":0,"readOnlyRootFilesystem":true,"allowPrivile...` | Container security context. Not privileged: every capability is dropped and only these are added back. Running as root is needed for BPF and for reading other processes' `/proc`, and the root filesystem stays read-only. For SELinux add `seLinuxOptions: {type: spc_t}`; where the node's AppArmor profile blocks BPF or debugfs, `appArmorProfile: {type: Unconfined}`. Full SysRq stacks need `privileged: true`, because runtimes mount `/proc/sysrq-trigger` read-only otherwise. Lists replace, so a changed `capabilities.add` has to repeat the ones it keeps. |
| `podSecurityContext` | object | `{}` | Pod security context. Empty on purpose, and `fsGroup` is refused: with it the kubelet makes the token group-readable, and the agent does not start with such a token. |
| `readinessProbe` | object | `{"exec":{"command":["molesignal-node-agent","health-check","--endpoint","read...` | Readiness probe: `/readyz` through the agent's own client, because the endpoint only listens on loopback. Not ready while it shuts down. |
| `livenessProbe` | object | `{"exec":{"command":["molesignal-node-agent","health-check","--endpoint","heal...` | Liveness probe: `/healthz`, the same way. |
| `resources` | object | `{"requests":{"cpu":"50m","memory":"96Mi"},"limits":{"memory":"512Mi"}}` | Resource requests and limits of the agent. |
| `updateStrategy` | object | `{"type":"RollingUpdate","rollingUpdate":{"maxUnavailable":1}}` | How a new version reaches the nodes. One node at a time by default; a pod's unsent batches stay in the state directory and the next pod on that node sends them. |
| `terminationGracePeriodSeconds` | int | `30` | Seconds between `SIGTERM` and `SIGKILL`. Has to be longer than the agent's shutdown window (`config.shutdownTimeoutMs`, 10 s by default). |
| `priorityClassName` | string | `"system-node-critical"` | Priority class of the pods, so that the agent is the last thing evicted from a node under pressure. Ignored when `priorityClass.create` is true. Some managed clusters only admit the `system-*` classes in namespaces with a ResourceQuota that names them; there, create such a quota, set `priorityClass.create`, or set this to `""`. |
| `priorityClass.create` | bool | `false` | Create a PriorityClass named after the release and give it to the pods instead of `priorityClassName`. PriorityClasses are cluster-wide. |
| `priorityClass.value` | int | `1000000` | Its value. Below the `system-*` classes (2000000000 and up). |
| `priorityClass.preemptionPolicy` | string | `"PreemptLowerPriority"` | `PreemptLowerPriority` or `Never`. |
| `namespace.create` | bool | `false` | Create the release's namespace with the chart, labelled for the `privileged` Pod Security level, which the agent needs: `baseline` already refuses host namespaces, host paths and added capabilities. Use instead of `--create-namespace`, and only for a namespace that does not exist yet. The namespace is kept on uninstall. When false, label the namespace yourself (the install notes say how). |
| `namespace.labels` | object | `{"pod-security.kubernetes.io/enforce":"privileged"}` | Labels of that namespace. |
| `namespace.annotations` | object | `{}` | Annotations of that namespace. |
| `serviceAccount.create` | bool | `true` | Create a service account. |
| `serviceAccount.name` | string | `""` | Name of the service account. Defaults to the full name. |
| `serviceAccount.annotations` | object | `{}` | Annotations for the service account. |
| `serviceAccount.automount` | bool | `false` | Mount the service account token into the pods. The agent does not call the Kubernetes API (pod names come from the kubelet's read-only port), so it is off. |
| `extraEnv` | list | `[]` | Extra environment variables for the agent, for example a proxy. Set `MS_*` variables in `config` instead. |
| `extraEnvFrom` | list | `[]` | Extra `envFrom` sources for the agent. |
| `extraVolumes` | list | `[]` | Extra volumes for the pods. |
| `extraVolumeMounts` | list | `[]` | Extra volume mounts for the agent. |
| `podAnnotations` | object | `{}` | Annotations added to the pods. |
| `podLabels` | object | `{}` | Labels added to the pods. |
| `nodeSelector` | object | `{"kubernetes.io/os":"linux"}` | Node selector. The agent is Linux-only (eBPF), and the tolerations below would also let it onto Windows nodes. |
| `tolerations` | list | `[{"operator":"Exists"}]` | Tolerations. Every taint by default, so that tainted nodes are observed too. |
| `affinity` | object | `{}` | Affinity. |
| `extraObjects` | list | `[]` | Additional Kubernetes manifests to deploy with the release. Each item is a complete object, and may use template expressions. |
<!-- values:end -->

## License

Apache License 2.0, like the agent.
