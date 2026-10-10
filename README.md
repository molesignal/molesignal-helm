# molesignal-helm

Helm charts for [MoleSignal](https://github.com/molesignal/molesignal), self-hosted,
OpenTelemetry-native observability: logs, metrics and traces on one Parquet / DataFusion engine.

[中文](README.zh-CN.md)

| Chart | Deploys |
| --- | --- |
| [`charts/molesignal`](charts/molesignal) | MoleSignal itself, as one pod (`standalone`) or as separate router / intake / querier / compactor / alert workloads (`distributed`), with an optional bundled PostgreSQL and RustFS for evaluation |

The chart is versioned on its own, apart from the application: see [versioning](docs/versioning.md).

## Install

```bash
helm repo add molesignal https://molesignal.github.io/molesignal-helm
helm repo update

# One MoleSignal pod plus a bundled PostgreSQL and RustFS, enough to try it out
helm install molesignal molesignal/molesignal \
  --namespace molesignal --create-namespace

kubectl --namespace molesignal port-forward svc/molesignal 5080:5080
# open http://127.0.0.1:5080
```

or, from the OCI registry:

```bash
helm install molesignal oci://ghcr.io/molesignal/charts/molesignal --version 0.1.0 \
  --namespace molesignal --create-namespace
```

The bundled PostgreSQL and RustFS are single instances without backups. **For production**
switch them off and use a managed PostgreSQL 17 and an S3 bucket:
[`examples/production.yaml`](examples/production.yaml).

Everything you can set, the secrets the chart manages (and the cipher key you must back up),
exposing the UI and OTLP, upgrading and uninstalling are in the
[chart README](charts/molesignal/README.md).

## Examples

| File | For |
| --- | --- |
| [`examples/production.yaml`](examples/production.yaml) | distributed mode, external PostgreSQL and S3, secrets you manage, TLS, autoscaling, disruption budgets |
| [`examples/aws-s3.yaml`](examples/aws-s3.yaml) | AWS S3 and an existing PostgreSQL (for example RDS) |
| [`examples/ingress-nginx.yaml`](examples/ingress-nginx.yaml) | UI and OTLP gRPC through ingress-nginx, with live-tail settings and `/metrics` blocked |
| [`examples/private-registry.yaml`](examples/private-registry.yaml) | every image through a mirror or an air-gapped registry |

## Repository layout

```
charts/molesignal/     the chart (templates, values, JSON schema, ci/ values for chart-testing)
examples/              values files for common setups
docs/                  versioning, publishing, how the chart is put together
tests/                 offline render tests and mutation tests (Python, no cluster needed)
scripts/               generator for the values table of the chart README
.github/workflows/     lint + test on every change, release on every new chart version
```

## Development

Needs `helm` 3.8+, `yq` (mikefarah, v4) and Python 3.11+.

```bash
make check     # lint, docs check, render tests, mutation tests: all offline
make package   # build the .tgz into ./dist
```

[`make test`](tests/run.py) renders the chart for a matrix of values and checks the manifests
(what is created, that selectors, ports, secrets and volumes refer to things that exist, the
generated `config.toml`, the guards against bad value combinations). [`make mutate`](tests/mutate.py)
breaks the chart on purpose and checks that the tests notice. Neither contacts a cluster:
`KUBECONFIG` is pointed at `/dev/null` for every call. CI additionally validates the manifests
with kubeconform and installs the chart on a kind cluster. See [CONTRIBUTING](CONTRIBUTING.md).

## Releases and hosting

Merging a pull request that bumps the chart version publishes it to GitHub Pages (a classic Helm
repository) and to ghcr.io (OCI). The one-time setup, and the optional custom domain, are in
[publishing](docs/publishing.md).

## License

Apache License 2.0, like the application. See [LICENSE](LICENSE).
