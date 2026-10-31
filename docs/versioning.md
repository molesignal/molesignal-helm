# Versioning

Each chart and the application are versioned separately. This is deliberate: a fix to a
template, a new value or a better default is a chart release even when the application did
not change, and a new application release is not always a chart release. The two charts,
`molesignal` and `probe-agent`, have nothing to do with each other's versions either: each has its own
`Chart.yaml`, its own GitHub releases (`molesignal-<version>`, `probe-agent-<version>`) and its own
package on ghcr.io, and changing one does not release the other.

| Where | What | Format |
| --- | --- | --- |
| `version` in `Chart.yaml` | the **chart** | SemVer, `0.1.0` |
| `appVersion` in `Chart.yaml` | the image the chart deploys by default, of the application or of the Agent | `latest` |
| `image.tag` in the values | overrides `appVersion` for one install | any published tag |

The application's versions have four numbers and are not SemVer; the chart's are, because
Helm requires it. The two never need to match.

The `molesignal` chart deploys the application's `latest` image (and RustFS's and the AWS CLI's; only
PostgreSQL is pinned, to 17), and the `probe-agent` chart the Agent's, so a new application release does
not need a new chart. What does need one is a change in something a chart mirrors from the
application: a role name, a setting, a port, a probe in `deploy/k8s`; a flag of `probe-agent`, or
how it starts Chromium. Each changelog entry says which state of the application it was
written against. CI installs the charts on kind once a week, so a `latest` that no longer starts with
them shows up without anyone changing a chart. A renamed setting does not fail that install: most
tables of the application's configuration ignore unknown keys, so check the chart's examples and
docs against `conf/config.toml` when the application renames one.

## When to bump what

Every change to a chart needs a new `version` of that chart, and **a published version is never
changed**. Fix forward.

While a chart is `0.x`, a minor bump may contain breaking changes; they are listed under
*Upgrade notes* in the [changelog](../CHANGELOG.md). From `1.0.0` on:

| Bump | When | Examples |
| --- | --- | --- |
| **patch** | a fix or a doc change that needs no action from anyone | a wrong probe path, a typo in a description, a template whitespace bug |
| **minor** | something new that is off or invisible by default | a new value, a new optional resource, a new `appVersion` that upgrades cleanly |
| **major** | an upgrade needs action | a value renamed or removed, a changed default that alters behaviour, a changed selector or volume claim, a higher minimum Kubernetes version |

Two things in particular are **breaking** because Kubernetes does not allow changing them in
place: the label selector of a Deployment or StatefulSet, and the `volumeClaimTemplates` of a
StatefulSet. The tests keep the selectors to the three stable labels
(`app.kubernetes.io/name`, `instance`, `component`) and never add `commonLabels` to them.

## Pinning the application version

`latest` is convenient and not reproducible. For production set `image.tag` (or `image.digest`),
the RustFS tag, and `pullPolicy: IfNotPresent`; [`examples/production.yaml`](../examples/production.yaml)
shows where. When the application changes in a way that needs a chart change, release the chart
first or together with it, and say in the changelog from which application state on it works.

The application and the Agent are released together, under the same tags, so CI installs both from
one `MOLESIGNAL_IMAGE_TAG`. An Agent may be older than its server, and then only gets the checks it can run.

The images a chart uses are listed in three places that must agree: `values.yaml`, the
`artifacthub.io/images` annotation in `Chart.yaml`, and the README. A test compares the first two.

## Releasing

1. In a pull request, bump `version` in the chart's `Chart.yaml`, for example
   `charts/molesignal/Chart.yaml`, and add a section to [`CHANGELOG.md`](../CHANGELOG.md).
2. Merge it. The [release workflow](../.github/workflows/release.yaml) packages the charts that changed,
   creates the GitHub release `<chart>-<version>`, updates the index on the `gh-pages`
   branch and pushes the same package to `ghcr.io/molesignal/charts/<chart>`. A version that already
   exists, in either place, is left alone.

See [publishing](publishing.md) for the one-time setup of the repository.
