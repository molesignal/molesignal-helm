# Contributing

Thanks for helping. This is a small repository: one chart, with tests that run in seconds
and need no cluster.

## Setup

You need `helm` 3.8 or later, [`yq`](https://github.com/mikefarah/yq) v4 (mikefarah's, not the
Python one) and Python 3.11 or later. Then:

```bash
make check
```

runs everything CI runs without a cluster: `helm lint --strict` for the defaults and every file in
`charts/molesignal/ci` and `examples`, the check that the values table in the chart README is
current, the render tests and the mutation tests. CI adds a kubeconform pass and an install on
kind.

The install job in CI needs an application image it can pull: `ghcr.io/molesignal/molesignal:latest`
unless the repository variable `MOLESIGNAL_IMAGE_TAG` says otherwise (see [publishing](docs/publishing.md)).
`scripts/ci-install.sh` is what it runs on kind; its control flow is tested with stand-ins for `helm`
and `kubectl`.

The scripts point `KUBECONFIG` at `/dev/null`. Keep it that way: nothing here needs a cluster, and
a test must never be able to touch yours.

## Changing the chart

1. **Add a value** to `values.yaml` with a `# --` description (what it is, what it does, the unit
   or the allowed values). `make docs` regenerates the table in the chart README from those comments, and
   fails on a value without one.
2. **Constrain it** in `values.schema.json` when the type or the allowed values are known. The
   top level is strict, so a typo in a key is an error rather than silence.
3. **Test it**: a scenario in `tests/scenarios_*.py` that renders with the value and checks the
   result. Look at what is already there; the harness checks that every selector, port, secret
   key and volume the manifests mention exists, on every render you pass to `integrity()`.
4. **Check the tests can fail.** If you changed logic rather than adding a value, add a mutation
   to `tests/mutate.py` that breaks it, and see that `make mutate` reports it as caught.
5. **Bump the chart version** in `Chart.yaml` and add a line to [`CHANGELOG.md`](CHANGELOG.md).
   `ct lint` fails the pull request otherwise, and a published version is never edited
   ([versioning](docs/versioning.md)).

### Things that are easy to get wrong

- **Selectors and volume claim templates cannot change in place.** Do not add labels to a
  selector or alter `volumeClaimTemplates` without a major version.
- **Do not ship default secrets.** No password, key or token has a default value; the chart
  generates them. A test checks the defaults.
- **Never put a secret in the ConfigMap.** Secrets are environment variables from Secrets.
- **Keep the three image lists in step** when you change an image: `values.yaml`, the
  `artifacthub.io/images` annotation in `Chart.yaml`, and `docs/`. A test compares the first two.
- The object store of the bundled sandbox is **RustFS**. Do not write "MinIO".
- `helm template` cannot `lookup`. Anything that depends on the cluster's current state must be
  testable through the hook in `tests/harness.py` (`chart_variant(fake_lookup=True)`).

## Commits and pull requests

Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org), like the
application repository: `feat(chart): …`, `fix(chart): …`, `docs: …`, `test: …`, `ci: …`. Describe
what changed and why in the body, and what you checked.

## Releasing

Merge a pull request that bumps the chart version. The release workflow does the rest; the
one-time repository setup is in [publishing](docs/publishing.md).
