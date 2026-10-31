#!/usr/bin/env bash
# Installs the charts on the cluster that kubectl points at (CI runs it on a throw-away kind
# cluster), checks them, and uninstalls. When something fails it prints what every pod and container
# said, including the previous run of a container that crashed, before it cleans up: the evidence is
# gone afterwards.
#
#   IMAGE_TAG=latest scripts/ci-install.sh               # both charts
#   IMAGE_TAG=latest scripts/ci-install.sh probe-agent   # one of them
#
# molesignal   once per values file in charts/molesignal/ci, followed by `helm test`.
# probe-agent  an Agent needs a server, so this installs the molesignal chart first and signs in to
#              it. Then, once per values file in charts/probe-agent/ci: a location and a token of
#              the kind the file is named after, `helm install`, `helm test`, every replica online,
#              a real HTTP and a real browser check through the Agents, and a deleted pod that comes
#              back as the same Agent.
#
# IMAGE_TAG        tag of the application and Agent images to install (default: latest)
# INSTALL_TIMEOUT  how long `helm install --wait` may take (default: 10m)
#
# Never run this against a cluster you care about: it creates and deletes namespaces.
set -uo pipefail

molesignal="charts/molesignal"
probe_agent="charts/probe-agent"
tag="${IMAGE_TAG:-latest}"
timeout="${INSTALL_TIMEOUT:-10m}"

# The server of the probe-agent install, as named in charts/probe-agent/ci.
agent_ns="ci-probe-agent"
server="ci-molesignal"
server_email="ci@example.com"
api_port="${API_PORT:-15080}"
poll_seconds="${POLL_SECONDS:-5}"
poll_attempts="${POLL_ATTEMPTS:-36}"
cookies=""
session_token=""
port_forward=""

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

install_molesignal() {
  local status=0 values name ns
  for values in "${molesignal}"/ci/*.yaml; do
    name="$(basename "${values}" -values.yaml)"
    ns="ci-${name}"
    echo "==> ${values}: application image tag ${tag}, namespace ${ns}"

    if helm install ci "${molesignal}" --namespace "${ns}" --create-namespace \
      --values "${values}" --set "image.tag=${tag}" --wait --timeout "${timeout}"; then
      # No --logs: the test pod is deleted as soon as it succeeds, so helm could not read them
      # and would fail a test that passed. A pod that fails stays, and dump prints its logs.
      if ! helm test ci --namespace "${ns}" --timeout 5m; then
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
  return "${status}"
}

# ── the server's API ──────────────────────────────────────────────────────────

# api METHOD PATH [JSON]: prints the body of the answer, and fails with it on an HTTP error.
api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(--silent --show-error --fail-with-body --max-time 30 --request "${method}"
    --cookie "${cookies}" --cookie-jar "${cookies}" --header "Content-Type: application/json")
  [[ -z "${session_token}" ]] || args+=(--header "Authorization: Bearer ${session_token}")
  [[ -z "${body}" ]] || args+=(--data "${body}")
  curl "${args[@]}" "http://127.0.0.1:${api_port}/api/v1${path}"
}

# Up to and including application 26.2.0.0 signing in answers with a token for the Authorization
# header. Later versions set a session cookie instead, which the jar in `api` keeps.
sign_in() {
  local password="$1" answer
  answer="$(api POST /auth/signin "$(jq -n --arg email "${server_email}" --arg password "${password}" \
    '{email: $email, password: $password}')")" || { echo "cannot sign in: ${answer}" >&2; return 1; }
  session_token="$(jq -r '.token // empty' <<<"${answer}")"
}

open_api() {
  local ns="$1" attempt
  cookies="$(mktemp)"
  kubectl --namespace "${ns}" port-forward "svc/${server}" "${api_port}:5080" >/dev/null &
  port_forward="$!"
  for ((attempt = 0; attempt < poll_attempts; attempt++)); do
    api GET /healthz >/dev/null 2>&1 && return 0
    sleep "${poll_seconds}"
  done
  echo "the API did not answer through the port-forward" >&2
  return 1
}

close_api() {
  [[ -z "${port_forward}" ]] || kill "${port_forward}" 2>/dev/null
  [[ -z "${cookies}" ]] || rm -f "${cookies}"
  port_forward=""
}

# ── the probe-agent install ───────────────────────────────────────────────────

# wait_for_agents LOCATION COUNT: prints the Agents once exactly COUNT exist and all are online.
wait_for_agents() {
  local location="$1" count="$2" agents="" attempt
  for ((attempt = 0; attempt < poll_attempts; attempt++)); do
    agents="$(api GET "/synthetics/locations/${location}/agents")" || return 1
    if jq -e --argjson count "${count}" \
      'length == $count and all(.[]; .status == "online")' <<<"${agents}" >/dev/null; then
      printf '%s' "${agents}"
      return 0
    fi
    sleep "${poll_seconds}"
  done
  echo "expected ${count} online Agents in the location, got: ${agents}" >&2
  return 1
}

# run_check NAME LOCATION SPEC: a test run of a new check, and it has to come back healthy from an
# Agent of the location. This is what proves the Agent can do its job, Chromium included.
run_check() {
  local name="$1" location="$2" spec="$3" created monitor revision results outcome attempt
  created="$(api POST /synthetics/monitors "$(jq -n --arg name "${name}" --arg location "${location}" --argjson spec "${spec}" \
    '{name: $name, spec: $spec, schedule: {kind: "interval", every_seconds: 300}, timeout_millis: 60000,
      location_policy: {kind: "any"}, location_ids: [$location], team_id: null, escalation_policy_id: null}')")" \
    || { echo "cannot create the check ${name}: ${created}" >&2; return 1; }
  monitor="$(jq -r .monitor.id <<<"${created}")"
  revision="$(jq -r .revision.id <<<"${created}")"
  api POST "/synthetics/monitors/${monitor}/revisions/${revision}/test" >/dev/null || return 1

  for ((attempt = 0; attempt < poll_attempts; attempt++)); do
    results="$(api GET "/synthetics/monitors/${monitor}/results?limit=5")" || return 1
    if jq -e 'length > 0' <<<"${results}" >/dev/null; then
      outcome="$(jq -r '.[0].outcome' <<<"${results}")"
      echo "check ${name}: ${outcome} on Agent $(jq -r '.[0].agent_id' <<<"${results}")"
      [[ "${outcome}" == "healthy" ]] && return 0
      echo "${results}" >&2
      return 1
    fi
    sleep "${poll_seconds}"
  done
  echo "no result for the check ${name}" >&2
  return 1
}

run_checks() {
  local location="$1" url="http://${server}.${agent_ns}.svc.cluster.local:5080"
  run_check http "${location}" "$(jq -n --arg url "${url}/api/v1/healthz" \
    '{kind: "http", configuration: {follow_redirects: true, max_redirects: 3, verify_tls: true,
      steps: [{id: "health", name: "health", method: "GET", url: {source: "literal", value: $url}, body: null}]}}')" || return 1
  run_check browser "${location}" "$(jq -n --arg url "${url}/" \
    '{kind: "browser", configuration: {viewport: {width: 1280, height: 720}, user_agent: null,
      capture_screenshot_on_failure: false, capture_har_on_failure: false,
      steps: [{id: "open", name: "open", action: {action: "navigate", url: {source: "literal", value: $url}, wait_until: "load"}}]}}')"
}

# same_agent_after_restart FULLNAME LOCATION AGENTS: a pod that is deleted comes back with its
# identity. Its init container says it found one, the location has no new Agent, and the Agent's
# heartbeat, which stopped with the old pod, moves again.
same_agent_after_restart() {
  local full="$1" location="$2" before="$3" pod="$1-0" stopped="" agents="" attempt
  kubectl --namespace "${agent_ns}" delete pod "${pod}" --wait=true --timeout=2m || return 1
  stopped="$(api GET "/synthetics/locations/${location}/agents" | jq -r --arg pod "${pod}" '.[] | select(.hostname == $pod) | .last_heartbeat_at')"
  kubectl --namespace "${agent_ns}" rollout status "statefulset/${full}" --timeout=5m || return 1

  for ((attempt = 0; attempt < poll_attempts; attempt++)); do
    agents="$(api GET "/synthetics/locations/${location}/agents")" || return 1
    if jq -e --arg pod "${pod}" --argjson stopped "${stopped}" \
      'any(.[]; .hostname == $pod and .status == "online" and .last_heartbeat_at > $stopped)' <<<"${agents}" >/dev/null; then
      if [[ "$(jq -c '[.[].id] | sort' <<<"${agents}")" != "$(jq -c '[.[].id] | sort' <<<"${before}")" ]]; then
        echo "the Agents changed: ${before} became ${agents}" >&2
        return 1
      fi
      kubectl --namespace "${agent_ns}" logs "${pod}" --container register | grep "already registered" \
        || { echo "${pod} did not find its identity" >&2; return 1; }
      echo "${pod} came back as the same Agent"
      return 0
    fi
    sleep "${poll_seconds}"
  done
  echo "${pod} did not come back online: ${agents}" >&2
  return 1
}

# One values file in charts/probe-agent/ci. Its name says which kind of token it registers with.
probe_agent_scenario() {
  local values="$1" kind release full location instructions token ca replicas agents
  kind="$(basename "${values}" -values.yaml)"
  release="${kind}"
  full="${release}-probe-agent"
  echo "==> ${values}: Agent image tag ${tag}"

  location="$(api POST /synthetics/locations "$(jq -n --arg code "${kind}" \
    '{name: $code, code: $code, egress_policy: {allow_private_networks: true}}')" | jq -r .id)"
  [[ -n "${location}" ]] || { echo "cannot create a location" >&2; return 1; }

  case "${kind}" in
    one-time-token)
      instructions="$(api POST "/synthetics/locations/${location}/register-tokens" '{"ttl_minutes": 30}')" || return 1
      token="$(jq -r .register_token <<<"${instructions}")"
      ;;
    agent-token)
      instructions="$(api POST /synthetics/agent-tokens "$(jq -n --arg location "${location}" \
        '{name: "ci", location_id: $location, expires_in_days: 1}')")" || return 1
      token="$(jq -r .agent_token <<<"${instructions}")"
      ;;
    *)
      echo "no token kind for ${values}" >&2
      return 1
      ;;
  esac
  echo "::add-mask::${token}"
  ca="$(jq -r '.ca_certificate_pem | @base64' <<<"${instructions}")"
  kubectl --namespace "${agent_ns}" create secret generic "ci-${kind}" --from-literal="token=${token}" || return 1

  helm install "${release}" "${probe_agent}" --namespace "${agent_ns}" --values "${values}" \
    --set "image.tag=${tag}" --set-string "registration.caCertificateBase64=${ca}" --wait --timeout "${timeout}" || return 1
  helm test "${release}" --namespace "${agent_ns}" --timeout 5m || return 1

  replicas="$(kubectl --namespace "${agent_ns}" get "statefulset/${full}" --output 'jsonpath={.spec.replicas}')"
  agents="$(wait_for_agents "${location}" "${replicas}")" || return 1
  echo "${replicas} online, each with its own identity: $(jq -c '[.[] | {hostname, status, capabilities}]' <<<"${agents}")"
  [[ "$(jq '[.[].id] | unique | length' <<<"${agents}")" == "${replicas}" ]] || { echo "Agents share an identity: ${agents}" >&2; return 1; }

  run_checks "${location}" || return 1
  same_agent_after_restart "${full}" "${location}" "${agents}" || return 1
}

install_probe_agent() {
  local status=0 password values
  echo "==> ${molesignal} as the server for ${probe_agent}: application image tag ${tag}, namespace ${agent_ns}"
  password="$(openssl rand -hex 24)"
  echo "::add-mask::${password}"
  kubectl create namespace "${agent_ns}" || status=1
  kubectl --namespace "${agent_ns}" create secret generic ci-root --from-literal="password=${password}" || status=1

  if [[ "${status}" == 0 ]] \
    && helm install ci "${molesignal}" --namespace "${agent_ns}" --values "${probe_agent}/ci/molesignal-server.yaml" \
      --set "image.tag=${tag}" --wait --timeout "${timeout}" \
    && open_api "${agent_ns}" && sign_in "${password}"; then
    for values in "${probe_agent}"/ci/*-values.yaml; do
      if ! probe_agent_scenario "${values}"; then
        echo "::error title=probe-agent::${values} failed"
        status=1
      fi
    done
  else
    status=1
  fi

  [[ "${status}" == 0 ]] || dump "${agent_ns}"

  close_api
  for values in "${probe_agent}"/ci/*-values.yaml; do
    helm uninstall "$(basename "${values}" -values.yaml)" --namespace "${agent_ns}" --wait --timeout 5m || true
  done
  helm uninstall ci --namespace "${agent_ns}" --wait --timeout 5m || true
  kubectl delete namespace "${agent_ns}" --wait=false || true
  return "${status}"
}

[[ "$#" -gt 0 ]] || set -- molesignal probe-agent

status=0
for chart in "$@"; do
  case "${chart}" in
    molesignal) install_molesignal || status=1 ;;
    probe-agent) install_probe_agent || status=1 ;;
    *)
      echo "unknown chart: ${chart}" >&2
      status=1
      ;;
  esac
done

exit "${status}"
