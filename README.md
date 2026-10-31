# molesignal-helm

Helm charts for [MoleSignal](https://github.com/molesignal/molesignal), self-hosted,
OpenTelemetry-native observability: logs, metrics and traces on one Parquet / DataFusion engine.

[中文](README.zh-CN.md)

| Chart | Deploys |
| --- | --- |
| [`charts/molesignal`](charts/molesignal) | MoleSignal itself, as one pod (`standalone`) or as separate router / intake / querier / compactor / alert workloads (`distributed`), with an optional bundled PostgreSQL and RustFS for evaluation |
| [`charts/probe-agent`](charts/probe-agent) | Probe Agents, which run synthetic checks from a private location and connect to a MoleSignal server, in this cluster or elsewhere |

Each chart is versioned on its own, apart from the other and from the application: see
[versioning](docs/versioning.md).

## Install

```bash
helm repo add molesignal https://charts.molesignal.com
helm repo update

# One MoleSignal pod plus a bundled PostgreSQL and RustFS, enough to try it out
helm install molesignal molesignal/molesignal \
  --namespace molesignal --create-namespace

kubectl --namespace molesignal port-forward svc/molesignal 5080:5080
# open http://127.0.0.1:5080
```

or, from the OCI registry:

```bash
helm install molesignal oci://ghcr.io/molesignal/charts/molesignal --version 0.2.0 \
  --namespace molesignal --create-namespace
```

The bundled PostgreSQL and RustFS are single instances without backups. **For production**
switch them off and use a managed PostgreSQL 17 and an S3 bucket:
[`examples/production.yaml`](examples/production.yaml).

Everything you can set, the secrets the chart manages (and the cipher key you must back up),
exposing the UI and OTLP, upgrading and uninstalling are in the
[chart README](charts/molesignal/README.md).

To run synthetic checks from inside a network, install Probe Agents next. They need a location and a
token from the server, and the server needs an address they can reach:
[`charts/probe-agent`](charts/probe-agent/README.md).

## Examples

| File | For |
| --- | --- |
| [`examples/production.yaml`](examples/production.yaml) | distributed mode, external PostgreSQL and S3, secrets you manage, TLS, autoscaling, disruption budgets |
| [`examples/aws-s3.yaml`](examples/aws-s3.yaml) | AWS S3 and an existing PostgreSQL (for example RDS) |
| [`examples/ingress-nginx.yaml`](examples/ingress-nginx.yaml) | UI and OTLP gRPC through ingress-nginx, with live-tail settings and `/metrics` blocked |
| [`examples/private-registry.yaml`](examples/private-registry.yaml) | every image through a mirror or an air-gapped registry |
| [`examples/probe-agent/agent-pool.yaml`](examples/probe-agent/agent-pool.yaml) | for the `probe-agent` chart: several Agents with an Agent Token in a Secret you manage |

## Repository layout

```
charts/molesignal/     the server's chart (templates, values, JSON schema, ci/ values for chart-testing)
charts/probe-agent/    the Agents' chart, laid out the same way
examples/              values files for common setups (examples/probe-agent/ for the Agents)
docs/                  versioning, publishing, how the chart is put together
tests/                 offline render tests and mutation tests (Python, no cluster needed)
scripts/               generator for the values tables of the chart READMEs, and the install on kind
.github/workflows/     lint + test on every change, release on every new chart version
```

## Development

Needs `helm` 3.8+, `yq` (mikefarah, v4) and Python 3.11+.

```bash
make check     # lint, docs check, render tests, mutation tests: all offline
make package   # build the .tgz into ./dist
```

[`make test`](tests/run.py) renders the charts for a matrix of values and checks the manifests
(what is created, that selectors, ports, secrets and volumes refer to things that exist, the
generated `config.toml`, the guards against bad value combinations). [`make mutate`](tests/mutate.py)
breaks them on purpose and checks that the tests notice. Neither contacts a cluster:
`KUBECONFIG` is pointed at `/dev/null` for every call. CI additionally validates the manifests
with kubeconform and installs the charts on a kind cluster, whenever a chart changes and once a
week, with Agents that have to come online against a real server. See [CONTRIBUTING](CONTRIBUTING.md).

## Releases and hosting

Merging a pull request that bumps a chart's version publishes that chart to GitHub Pages (a classic
Helm repository) and to ghcr.io (OCI). The one-time setup, and the optional custom domain, are in
[publishing](docs/publishing.md).

## License

Apache License 2.0, like the application. See [LICENSE](LICENSE).
