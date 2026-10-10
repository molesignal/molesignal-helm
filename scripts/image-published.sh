#!/usr/bin/env bash
# Exit 0 when ghcr.io/<repository>:<tag> exists, 1 when it does not. Anonymous pull, so the
# image must be public. Needs curl and jq.
#
#   scripts/image-published.sh molesignal/molesignal latest
set -euo pipefail

repository="${1:?usage: image-published.sh <owner/name> <tag>}"
tag="${2:?usage: image-published.sh <owner/name> <tag>}"

token="$(curl -fsSL "https://ghcr.io/token?scope=repository:${repository}:pull" | jq -r '.token')"

code="$(curl -s -o /dev/null -w '%{http_code}' --head \
  --header "Authorization: Bearer ${token}" \
  --header 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
  "https://ghcr.io/v2/${repository}/manifests/${tag}")"

[ "${code}" = "200" ]
