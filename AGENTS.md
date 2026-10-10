# Working in this repository

One Helm chart (`charts/molesignal`) for the MoleSignal application, plus offline tests. The
application lives in `../molesignal`; its `deploy/k8s` manifests are what the chart mirrors, and its
`conf/config.toml` documents every setting.

## Commands

- `make check`: lint, docs check, render tests, mutation tests. All offline, a few seconds.
- `make docs`: regenerate the values table of `charts/molesignal/README.md` after editing
  `values.yaml` (every value needs a `# --` description, or the generator fails).
- `python3 tests/run.py -k <module>` runs one group of scenarios; `python3 tests/mutate.py -k <name>`
  one mutation.

## Rules

- **Never run helm or kubectl against a real cluster from here.** Every script sets
  `KUBECONFIG=/dev/null`; do the same by hand. `helm template` and `helm lint` need no cluster.
- Every chart change bumps `version` in `Chart.yaml` and adds a `CHANGELOG.md` line. A published
  version is never edited.
- No default secrets. No secret in the ConfigMap. Selectors and `volumeClaimTemplates` do not change.
- The bundled object store is **RustFS**; never write "MinIO".
- New value: `# --` description in `values.yaml`, an entry in `values.schema.json`, a scenario in
  `tests/`. New logic: a mutation in `tests/mutate.py` that the tests must catch.
- Conventional Commit subjects in English (`feat(chart): …`). No co-author trailers and no
  attribution footers in commits or pull requests.
- The chart has been rendered and linted, but a real install is only exercised by CI on kind. Do
  not describe behaviour that only a running cluster would prove as verified.
