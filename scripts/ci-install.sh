#!/usr/bin/env bash
# Installs the chart once per values file in charts/molesignal/ci on the cluster that kubectl
# points at (CI runs it on a throw-away kind cluster), runs `helm test`, and uninstalls. When
# something fails it prints what every pod and container said, including the previous run of a
# container that crashed, before it cleans up: the evidence is gone afterwards.
#
#   IMAGE_TAG=latest scripts/ci-install.sh
#
# IMAGE_TAG      tag of the application image to install (default: latest)
# INSTALL_TIMEOUT  how long `helm install --wait` may take (default: 10m)
#
# Never run this against a cluster you care about: it creates and deletes namespaces.
set -uo pipefail

chart="charts/molesignal"
tag="${IMAGE_TAG:-latest}"
timeout="${INSTALL_TIMEOUT:-10m}"

dump() {
  local ns="$1" pod container
  echo "::group::pods in ${ns}"
  kubectl --namespace "${ns}" get pods --output wide
  echo "::endgroup::"
  echo "::group::events in ${ns}"
  kubectl --namespace "${ns}" get events --sort-by=.lastTimestamp
  echo "::endgroup::"
  for pod in $(kubectl --namespace "${ns}" get pods --output name); do
    echo "::group::describe ${pod}"
    kubectl --namespace "${ns}" describe "${pod}"
    echo "::endgroup::"
    for container in $(kubectl --namespace "${ns}" get "${pod}" \
      --output jsonpath='{.spec.initContainers[*].name} {.spec.containers[*].name}'); do
      echo "::group::logs ${pod} / ${container}"
      kubectl --namespace "${ns}" logs "${pod}" --container "${container}" --tail=300 2>&1
      echo "--- previous run of ${container} (if it restarted):"
      kubectl --namespace "${ns}" logs "${pod}" --container "${container}" --previous --tail=100 2>&1
      echo "::endgroup::"
    done
  done
}

status=0
for values in "${chart}"/ci/*.yaml; do
  name="$(basename "${values}" -values.yaml)"
  ns="ci-${name}"
  echo "==> ${values}: application image tag ${tag}, namespace ${ns}"

  if helm install ci "${chart}" --namespace "${ns}" --create-namespace \
    --values "${values}" --set "image.tag=${tag}" --wait --timeout "${timeout}"; then
    if ! helm test ci --namespace "${ns}" --logs --timeout 5m; then
      status=1
      dump "${ns}"
    fi
  else
    status=1
    dump "${ns}"
  fi

  helm uninstall ci --namespace "${ns}" --wait --timeout 5m || true
  kubectl delete namespace "${ns}" --wait=false || true
done

exit "${status}"
