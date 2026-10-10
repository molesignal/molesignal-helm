# Publishing the chart

The chart is published to two places from the same package, and **neither needs a custom
domain**:

| Where | Install with | Needs |
| --- | --- | --- |
| GitHub Pages (a classic Helm repository) | `helm repo add molesignal https://molesignal.github.io/molesignal-helm` | GitHub Pages switched on for the `gh-pages` branch |
| GitHub Container Registry (OCI) | `helm install ... oci://ghcr.io/molesignal/charts/molesignal --version <v>` | the package set to public |

A custom domain such as `charts.molesignal.com` is optional, and only changes the first row
(see [below](#optional-a-custom-domain)).

## One-time setup

1. **Create the repository** `molesignal/molesignal-helm` on GitHub (public) and push `main`.
   If you use another name, search for `molesignal-helm` in this repository and replace it
   (README files, `Chart.yaml`, `cr.yaml`, the docs).
2. **Let the workflows write.** *Settings → Actions → General → Workflow permissions* should
   be *Read and write permissions* (or leave the default and let the `permissions:` blocks in
   the workflows do it, if your organization allows that).
3. **Merge the chart to `main`.** The [release workflow](../.github/workflows/release.yaml)
   then creates the `gh-pages` branch, the GitHub release `molesignal-0.1.0` and the package
   on ghcr.io.
4. **Switch on GitHub Pages.** *Settings → Pages → Build and deployment → Source: Deploy from a
   branch → `gh-pages` / `/ (root)`*. After a minute
   `https://molesignal.github.io/molesignal-helm/index.yaml` lists the chart.
5. **Make the package public.** A package pushed by a workflow starts out private. In the
   organization: *Packages → `charts/molesignal` → Package settings → Change visibility →
   Public*. Until then `helm pull` needs a login.
6. **Check it from a machine that is not yours:**

   ```bash
   helm repo add molesignal https://molesignal.github.io/molesignal-helm
   helm search repo molesignal
   helm pull oci://ghcr.io/molesignal/charts/molesignal --version 0.1.0
   ```

Every later release is just a merged pull request that bumps the chart version
([versioning](versioning.md)).

## Optional: Artifact Hub

[Artifact Hub](https://artifacthub.io) lists Helm charts and reads the annotations already in
`Chart.yaml`. Add the repository there as a *Helm charts* repository with the Pages URL (or an
*OCI* one with the ghcr.io address). To get the "verified publisher" mark, Artifact Hub asks for
an `artifacthub-repo.yml` with a repository ID and an owner e-mail in the repository root of
the `gh-pages` branch; that file holds an address, so it is not committed here.

## Optional: a custom domain

You would want one to give people a URL that does not change if the GitHub organization or the
repository is ever renamed or moved, for example:

```bash
helm repo add molesignal https://charts.molesignal.com
```

You do **not** need one for the OCI address, which stays on ghcr.io.

The setup is three steps, and the first two are on your side:

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
downloads keep working whatever you do with the domain. Only the repository address people
type depends on it.
