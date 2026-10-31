# Changelog

Notable changes to the `molesignal` chart. The chart is versioned on its own, apart from the
application (see [versioning](docs/versioning.md)); each entry names the application version the
chart deploys by default.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org). While the chart is `0.x` a minor version may contain
breaking changes; they are collected under **Upgrade notes**.

## [Unreleased]

## [0.2.0] - 2026-10-31

Deploys `ghcr.io/molesignal/molesignal:latest`, which is the application's 26.2.0.0 today. Written
against that release and against the settings the application's `main` has renamed since.

### Changed

- The `bucket-init` sidecar of the bundled RustFS pulls `amazon/aws-cli` from Docker Hub, where the
  chart's other public images live. It is the same image as `public.ecr.aws/aws-cli/aws-cli`, but ECR
  Public answered anonymous pulls from CI's shared addresses with `429 Too Many Requests`, and that
  held up the whole install: MoleSignal pods wait for RustFS, and RustFS is ready only once the sidecar
  has created the bucket.
- `releaseChannel` defaults to `stable`, the channel of the default image: `latest` only moves with
  stable-channel releases.

### Fixed

- **`standalone` pods, the default, never became ready.** The readiness probe asked `/api/v1/readyz`,
  which the application's full HTTP server answers with 401 without a login. It now asks
  `/api/v1/healthz`, like the router.
- The examples and docs no longer name settings the application has removed or renamed after 26.2.0.0
  (`compactor.retention_days`, `cache.object.max_bytes`, `intake.max_buffer_memory_mb`). Most tables of
  its configuration ignore unknown keys, so those would have stopped working without a word.
  `examples/production.yaml` no longer sets the retention, which was the default of 30 days anyway.
- The README no longer says that `latest` cannot be pulled. It says which application versions the
  chart needs: 26.2.0.0 or later.

### Added

- The README, the install notes and the ingress examples explain how to get real client addresses
  behind an ingress.

### Upgrade notes

- With `global.imageRegistry`, mirror the sidecar's image as `<registry>/amazon/aws-cli` instead of
  `<registry>/aws-cli/aws-cli`, or set `rustfs.bucketInit.image.repository`.
- `RELEASE_CHANNEL` changes from `alpha` to `stable` unless you set `releaseChannel`, so the upgrade
  rolls the pods. It is metadata: `/api/v1/healthz` reports it.
- If your `config` sets `compactor.retention_days`, `cache.object.max_bytes` or
  `intake.max_buffer_memory_mb`, check them against the `conf/config.toml` of the image you run.

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
