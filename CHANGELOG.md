# Changelog

Notable changes to the `molesignal` chart. The chart is versioned on its own, apart from the
application (see [versioning](docs/versioning.md)); each entry names the application version the
chart deploys by default.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org). While the chart is `0.x` a minor version may contain
breaking changes; they are collected under **Upgrade notes**.

## [Unreleased]

## [0.1.0] - 2026-10-11

First release. Deploys MoleSignal from `ghcr.io/molesignal/molesignal:latest`, written against the
application's `main` as of 2026-10-10 (commit `4a4d46b`).

> **Compatibility.** No image published so far matches that state of the application: `26.1.0.0` and
> `alpha` (built on 2026-08-17) still call the alert role `alert_manager` and configure the metadata
> store differently, so they do not start with this chart, and there is no `latest` tag yet (the
> application publishes it only for stable-channel releases). Publish an image built from `main`
> before relying on the defaults.

### Added

- Two topologies: `standalone` (one StatefulSet running every role) and `distributed` (router,
  intake, querier, compactor and alert as separate workloads), mirroring the application's
  `deploy/k8s` manifests.
- An optional bundled PostgreSQL 17 and RustFS for evaluation. The RustFS pod creates the
  bucket through a sidecar and is ready only once it exists; MoleSignal pods wait for both.
- `config.toml` written from the chart's own values and a `config` map that is merged over them,
  with integers kept as integers.
- Secrets: the cipher key, cluster token and the bundled services' credentials are generated on
  the first install and kept on upgrade (`lookup`), or supplied through values or existing
  Secrets. The generated Secrets are kept on uninstall.
- External PostgreSQL and S3-compatible object store, with inline credentials or existing Secrets.
- Ingress for the UI and for OTLP over gRPC, HPAs for router and querier, PodDisruptionBudgets,
  scheduling, security-context and probe overrides, `global.imageRegistry` for mirrors,
  `extraObjects`.
- Every image but PostgreSQL's follows `latest` and is pulled every time (`pullPolicy: Always`).
- `values.schema.json`, `helm test`, install notes, guards that refuse contradictory values.
- Offline render tests and mutation tests, kubeconform validation and a kind install in CI.

### Upgrade notes

None: this is the first release.
