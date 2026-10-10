# Publishing the chart

The chart is published to two places from the same package:

| Where | Install with | Needs |
| --- | --- | --- |
| A Helm repository on GitHub Pages | `helm repo add molesignal https://charts.molesignal.com` | GitHub Pages on the `gh-pages` branch, and the domain below |
| GitHub Container Registry (OCI) | `helm install ... oci://ghcr.io/molesignal/charts/molesignal --version <v>` | the package set to public |

The first address is the one the website's quick start already shows. **A custom domain is not
technically required**: GitHub Pages serves the same index at
`https://molesignal.github.io/molesignal-helm`, and the OCI address needs no domain at all. But
`charts.molesignal.com` is what people are told to type, so it has to exist before the website's
Helm tab is switched on. It also gives you an address that survives renaming or moving the
GitHub organization or repository.

## One-time setup

1. **Create the repository** `molesignal/molesignal-helm` on GitHub (public).
   If you use another name, search for `molesignal-helm` in this repository and replace it
   (README files, `Chart.yaml`, `cr.yaml`, the docs).
2. **Let the workflows write.** *Settings → Actions → General → Workflow permissions* should
   be *Read and write permissions* (or leave the default and let the `permissions:` blocks in
   the workflows do it, if your organization allows that).
3. **Publish the first version through a pull request**, so that the install test on kind runs
   before anything is published (a published version is never rewritten):

   ```bash
   git remote add origin git@github.com:molesignal/molesignal-helm.git
   git push origin "$(git rev-list --max-parents=0 main)":refs/heads/main   # only the first commit (the license) becomes main
   git push origin main:refs/heads/initial-chart      # everything else, as a branch
   ```

   Open a pull request from `initial-chart` to `main`. `Lint and test` runs `make check`,
   validates the manifests against the Kubernetes schemas and installs both topologies on kind. Fix what
   it finds, then merge.

   The install needs an application image. It uses `ghcr.io/molesignal/molesignal:latest`, and the
   job **fails with a message saying so if that tag does not exist**; the application's release
   workflow pushes `latest` only for stable-channel releases. Either publish an image built from the
   application's `main` under that tag, or set the repository variable `MOLESIGNAL_IMAGE_TAG`
   (*Settings → Secrets and variables → Actions → Variables*) to a tag that exists, such as the
   `sha-<commit>` tag of such a build, or pass `image_tag` when you run the workflow by hand. Do not
   merge before the install job has run and passed once: the merge publishes the chart. The merge triggers the [release workflow](../.github/workflows/release.yaml),
   which creates the `gh-pages` branch, the GitHub release `molesignal-0.1.0` and the package on ghcr.io.
4. **Switch on GitHub Pages.** *Settings → Pages → Build and deployment → Source: Deploy from a
   branch → `gh-pages` / `/ (root)`*. After a minute
   `https://molesignal.github.io/molesignal-helm/index.yaml` lists the chart.
5. **Make the package public.** A package pushed by a workflow starts out private. In the
   organization: *Packages → `charts/molesignal` → Package settings → Change visibility →
   Public*. Until then `helm pull` needs a login.
6. **Point `charts.molesignal.com` at it** (below).
7. **Check it from a machine that is not yours:**

   ```bash
   helm repo add molesignal https://charts.molesignal.com
   helm search repo molesignal
   helm pull oci://ghcr.io/molesignal/charts/molesignal --version 0.1.0
   ```

Every later release is just a merged pull request that bumps the chart version
([versioning](versioning.md)).

## The custom domain

1. **DNS.** Add a `CNAME` record `charts` → `molesignal.github.io`. If the zone is on Cloudflare,
   set the record to *DNS only* (grey cloud) while GitHub checks the domain and issues the
   certificate; proxying it is possible afterwards but adds nothing here. It does not affect
   `www.molesignal.com`, which is a separate record.
2. **GitHub.** *Settings → Pages → Custom domain*: enter `charts.molesignal.com`, save, wait for
   the DNS check to pass, then tick *Enforce HTTPS*. GitHub commits a `CNAME` file to
   `gh-pages`; the release workflow leaves other files on that branch alone.
3. **Hardening (recommended).** Verify `molesignal.com` for the organization (*Organization
   settings → Pages → Add a domain*) so that nobody else can claim a subdomain you have
   removed.

The chart archives themselves stay on the GitHub releases (`index.yaml` points at them), so
downloads keep working whatever happens to the domain. Only the address people type depends on it.

## The website

The website's quick start (`components/quick-start-tabs.tsx` in the website repository) already
has a Helm tab that points at this repository, behind the switch
`NEXT_PUBLIC_QUICKSTART_ARTIFACTS_READY`, together with the binary tab. Before switching it on:

- replace the `HELM_CMD` example: the `--set storage.s3.*` keys in it are not values of this chart
  (see [`examples/aws-s3.yaml`](../examples/aws-s3.yaml) for the real ones), and a plain
  `helm install` with no `--set` already gives a working evaluation install;
- the page's prerequisites say Kubernetes 1.27 and Helm 3; the chart requires 1.27 or later and
  Helm 3.8 or later;
- close the roadmap issue "Official Helm chart" as completed, so that the roadmap page shows it as done.

## Optional: Artifact Hub

[Artifact Hub](https://artifacthub.io) lists Helm charts and reads the annotations already in
`Chart.yaml`. Add the repository there as a *Helm charts* repository with the repository URL (or an
*OCI* one with the ghcr.io address). To get the "verified publisher" mark, Artifact Hub asks for
an `artifacthub-repo.yml` with a repository ID and an owner e-mail in the root of the
`gh-pages` branch; that file holds an address, so it is not committed here.
