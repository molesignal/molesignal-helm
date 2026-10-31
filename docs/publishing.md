# Publishing the charts

Each chart is published to two places from the same package. One Helm repository holds all of
them; on ghcr.io each chart is a package of its own:

| Where | Install with | Needs |
| --- | --- | --- |
| A Helm repository on GitHub Pages | `helm repo add molesignal https://charts.molesignal.com` | GitHub Pages on the `gh-pages` branch, and the domain below |
| GitHub Container Registry (OCI) | `helm install ... oci://ghcr.io/molesignal/charts/<chart> --version <v>` | the package set to public |

The first address is the one the website's quick start already shows. **A custom domain is not
technically required**: GitHub Pages serves the same index at
`https://molesignal.github.io/molesignal-helm`, and the OCI address needs no domain at all. But
`charts.molesignal.com` is what people are told to type. It also gives you an address that survives renaming or moving the
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

   The install needs images. It uses `ghcr.io/molesignal/molesignal:latest` and
   `ghcr.io/molesignal/probe-agent:latest`, and the job **fails with a message saying so if that tag
   does not exist**; the application's release workflow pushes `latest` only for stable-channel
   releases. Either publish images built from the application's `main` under that tag, or set the repository variable `MOLESIGNAL_IMAGE_TAG`
   (*Settings → Secrets and variables → Actions → Variables*) to a tag that exists, such as the
   `sha-<commit>` tag of such a build, or pass `image_tag` when you run the workflow by hand. Do not
   merge before the install job has run and passed once: the merge publishes the chart. The merge triggers the [release workflow](../.github/workflows/release.yaml),
   which creates the `gh-pages` branch, the GitHub release `molesignal-0.1.0` and the package on ghcr.io.
4. **Switch on GitHub Pages.** *Settings → Pages → Build and deployment → Source: Deploy from a
   branch → `gh-pages` / `/ (root)`*. After a minute
   `https://molesignal.github.io/molesignal-helm/index.yaml` lists the chart. (If Pages was switched
   on earlier with `main` as the source, GitHub committed a `CNAME` file to `main`. That is
   harmless, but while `main` is the source the repository itself is what gets published; change the
   source as soon as `gh-pages` exists. The release workflow puts the same `CNAME`, and a
   `.nojekyll`, on `gh-pages` when it creates it, so the domain survives the switch.)
5. **Check that the package is public.** `charts/probe-agent` could be pulled without a login right after
   its first release. If `helm pull` asks for one, change it in the organization: *Packages →
   `charts/<chart>` → Package settings → Change visibility → Public*. Check this once for every new chart.
6. **Point `charts.molesignal.com` at it** (below).
7. **Check it from a machine that is not yours:**

   ```bash
   helm repo add molesignal https://charts.molesignal.com
   helm search repo molesignal
   helm pull oci://ghcr.io/molesignal/charts/molesignal --version 0.1.0
   ```

Every later release is just a merged pull request that bumps a chart's version
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

### If the domain check does not pass

GitHub says "DNS check unsuccessful" when *its* resolver cannot see the record, and a resolver
that asked before the record existed keeps answering "no such domain" until the negative answer
expires. For a zone on Cloudflare that is the SOA's last field, 1800 seconds. So after creating the
record: leave it alone for half an hour (deleting and re-adding it only restarts the wait), then
press *Check again*. To see what GitHub sees, ask it:

```bash
gh api repos/molesignal/molesignal-helm/pages/health
```

`dns_resolves`, `is_valid` and `is_cname_to_github_user_domain` should be `true` and `is_proxied`
`false`. `https_error: peer_failed_verification` with `is_https_eligible: true` only means the
certificate has not been issued yet; "Enforce HTTPS" stays greyed out until it is, usually within an
hour. What the rest of the internet sees, independent of any local DNS tool or proxy (some of them
rewrite answers to the reserved `198.18.0.0/15` range):

```bash
curl -s 'https://cloudflare-dns.com/dns-query?name=charts.molesignal.com&type=CNAME' -H 'accept: application/dns-json'
```

It should answer `molesignal.github.io.`. If the record is shown with the orange cloud in
Cloudflare, switch it to *DNS only*. A `CAA` record on the zone must allow `letsencrypt.org`.

Verifying the domain for the organization (step 3 above) is a separate check with its own TXT
record, `_github-pages-challenge-molesignal` on `molesignal.com`, whose value GitHub shows you. It is
not needed for the chart to work.

The chart archives themselves stay on the GitHub releases (`index.yaml` points at them), so
downloads keep working whatever happens to the domain. Only the address people type depends on it.

## The website

The website's quick start shows the Helm install command (`lib/commands.ts` in the website
repository), and its tests check that it names `charts.molesignal.com`. Change it there when the way
to install changes, and run the new command first.

## Optional: Artifact Hub

[Artifact Hub](https://artifacthub.io) lists Helm charts and reads the annotations already in
`Chart.yaml`. Add the repository there as a *Helm charts* repository with the repository URL (or an
*OCI* one with the ghcr.io address). To get the "verified publisher" mark, Artifact Hub asks for
an `artifacthub-repo.yml` with a repository ID and an owner e-mail in the root of the
`gh-pages` branch; that file holds an address, so it is not committed here.
