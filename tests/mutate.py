#!/usr/bin/env python3
"""Break the chart on purpose, one way at a time, and check that the tests notice.

    python3 tests/mutate.py            # every mutation
    python3 tests/mutate.py -k secret  # mutations whose name contains "secret"

A mutation that no check catches is a hole in the tests: the script reports it and
exits non-zero. Each mutation runs only the scenario modules that cover it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# name, file in the chart, exact text, replacement, scenario modules that must catch it
MUTATIONS = [
    ("secret-reuse-ignored", "templates/_helpers.tpl",
     "{{- if and $existing $existing.data (hasKey $existing.data .key) -}}", "{{- if false -}}", "secrets"),
    ("secret-not-kept", "templates/secret.yaml",
     "    helm.sh/resource-policy: keep\n", "", "install"),
    ("explicit-secret-ignored", "templates/_helpers.tpl",
     "{{- if .value -}}\n{{- .value | b64enc -}}\n{{- else -}}", "{{- if false -}}\n{{- .value | b64enc -}}\n{{- else -}}", "secrets"),
    ("toml-writes-floats", "templates/_helpers.tpl",
     "{{- if eq (floor .) . -}}{{ int64 . }}{{- else -}}{{ . }}{{- end -}}", "{{ . }}", "config"),
    ("toml-no-quoting", "templates/_helpers.tpl",
     '{{- if regexMatch "^[A-Za-z0-9_-]+$" . -}}{{ . }}{{- else -}}{{ toJson . }}{{- end -}}', "{{ . }}", "config"),
    ("user-config-loses", "templates/_helpers.tpl",
     '"base" (include "molesignal.config.base" . | fromJson) "over" (.Values.config | default dict)',
     '"over" (include "molesignal.config.base" . | fromJson) "base" (.Values.config | default dict)', "config"),
    ("null-does-not-remove", "templates/_helpers.tpl",
     '{{- if kindIs "invalid" $v -}}\n{{- $_ := unset $out $k -}}', '{{- if false -}}\n{{- $_ := unset $out $k -}}', "config"),
    ("router-advertises", "templates/_helpers.tpl",
     "router:\n  kind: Deployment\n  wal: false\n  advertise: false", "router:\n  kind: Deployment\n  wal: false\n  advertise: true", "install"),
    ("compactor-gets-service", "templates/_helpers.tpl",
     "compactor:\n  kind: Deployment\n  wal: false\n  advertise: true\n  service: false",
     "compactor:\n  kind: Deployment\n  wal: false\n  advertise: true\n  service: true", "install"),
    ("service-selects-wrong-pods", "templates/services.yaml",
     '(dict "ctx" $ "component" $role) | nindent 4 }}\n  ports:', '(dict "ctx" $ "component" "router") | nindent 4 }}\n  ports:', "install"),
    ("probe-always-merged", "templates/_helpers.tpl",
     '{{- if or (hasKey $override "httpGet") (hasKey $override "tcpSocket") (hasKey $override "exec") (hasKey $override "grpc") -}}',
     "{{- if false -}}", "options"),
    ("registry-not-applied", "templates/_helpers.tpl",
     '{{- if $registry -}}\n{{- $parts', '{{- if false -}}\n{{- $parts', "options"),
    ("grace-period-lost", "values.yaml",
     "  terminationGracePeriodSeconds: 60\n  # -- `OrderedReady`", "  terminationGracePeriodSeconds: null\n  # -- `OrderedReady`", "install"),
    ("postgres-keeps-default-connections", "templates/postgresql.yaml",
     "            - -c\n            - max_connections={{ $pg.maxConnections }}\n", "", "install"),
    ("rustfs-pod-ready-too-early", "templates/rustfs.yaml",
     '              command: ["test", "-f", "/tmp/bucket-ready"]', '              command: ["true"]', "install"),
    ("rustfs-wrong-uid", "values.yaml",
     "  podSecurityContext:\n    fsGroup: 10001", "  podSecurityContext:\n    fsGroup: 1000", "install"),
    ("both-databases-allowed", "templates/_helpers.tpl",
     "{{- if or .Values.externalDatabase.dsn .Values.externalDatabase.existingSecret -}}\n{{- fail \"molesignal: `postgresql.enabled` is true",
     "{{- if false -}}\n{{- fail \"molesignal: `postgresql.enabled` is true", "guards"),
    ("password-not-checked", "templates/_helpers.tpl",
     '{{- if and .Values.postgresql.auth.password (not (regexMatch "^[A-Za-z0-9._~-]+$" .Values.postgresql.auth.password)) -}}',
     "{{- if false -}}", "guards"),
    ("schema-allows-typos", "values.schema.json",
     '"type": "object",\n  "additionalProperties": false,\n  "$defs"', '"type": "object",\n  "additionalProperties": true,\n  "$defs"', "guards"),
    ("hpa-keeps-replicas", "templates/workloads.yaml",
     "{{- if not (and (eq $spec.kind \"Deployment\") $roleCfg.autoscaling $roleCfg.autoscaling.enabled) }}", "{{- if true }}", "options"),
    ("checksum-includes-generated", "templates/_helpers.tpl",
     '{{- dict\n    "cipherKey"', '{{- dict "random" (randAlphaNum 8)\n    "cipherKey"', "install"),
    ("appversion-drifts-from-annotation", "Chart.yaml",
     "image: ghcr.io/molesignal/molesignal:latest", "image: ghcr.io/molesignal/molesignal:stable", "guards"),
    ("standalone-probe-needs-login", "templates/_helpers.tpl",
     "probe-register, probe-control]\n  probe: {path: /api/v1/healthz,", "probe-register, probe-control]\n  probe: {path: /api/v1/readyz,", "install"),
    ("channel-drifts-from-latest", "values.yaml",
     "releaseChannel: stable", "releaseChannel: alpha", "guards"),
    ("notes-forget-client-addresses", "templates/_notes.tpl",
     "   trusted proxies: in the `_sys` organization, Settings > Client IP recognition.\n", "", "guards"),
]

# The same for charts/probe-agent; the module that covers it is always probe_agent.
PROBE_AGENT_MUTATIONS = [
    ("registers-over-an-identity", "templates/statefulset.yaml",
     '''              if [ -e "$STATE_DIR/identity.json" ]; then
                echo "already registered: using the identity in $STATE_DIR"
                exit 0
              fi
''', "", "probe_agent"),
    ("state-at-the-volume-root", "templates/_helpers.tpl",
     '{{- include "probe-agent.volumeMountPath" . }}/state', '{{- include "probe-agent.volumeMountPath" . }}', "probe_agent"),
    ("agent-runs-elsewhere", "templates/statefulset.yaml",
     "            - --state-dir\n            - {{ $stateDir }}\n", "", "probe_agent"),
    ("loses-dumb-init", "templates/statefulset.yaml",
     '''        - name: probe-agent
          image: {{ include "probe-agent.image" . }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args:
''', '''        - name: probe-agent
          image: {{ include "probe-agent.image" . }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["probe-agent"]
          args:
''', "probe_agent"),
    ("one-claim-for-all", "templates/statefulset.yaml",
     "  {{- if .Values.persistence.enabled }}\n  volumeClaimTemplates:", "  {{- if false }}\n  volumeClaimTemplates:", "probe_agent"),
    ("replicas-wait-for-each-other", "templates/statefulset.yaml",
     "  podManagementPolicy: Parallel\n", "", "probe_agent"),
    ("token-in-the-pod-spec", "templates/statefulset.yaml",
     '''            - name: REGISTER_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {{ include "probe-agent.tokenSecretName" . }}
                  key: {{ .Values.registration.existingSecretKey }}
                  # Optional: the Secret can go once the Agents have an identity, and a replica
                  # without one then says so itself instead of hanging in CreateContainerConfigError.
                  optional: true
''', '''            - name: REGISTER_TOKEN
              value: {{ .Values.registration.token | quote }}
''', "probe_agent"),
    ("secret-is-required", "templates/statefulset.yaml",
     "                  optional: true\n", "", "probe_agent"),
    ("token-in-the-running-agent", "templates/statefulset.yaml",
     '''          env:
            - name: RUST_LOG
              value: {{ .Values.logLevel | quote }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.extraEnvFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.resources }}
''', '''          env:
            - name: REGISTER_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {{ include "probe-agent.tokenSecretName" . }}
                  key: {{ .Values.registration.existingSecretKey }}
                  optional: true
            - name: RUST_LOG
              value: {{ .Values.logLevel | quote }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.extraEnvFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.resources }}
''', "probe_agent"),
    ("new-token-restarts-agents", "templates/statefulset.yaml",
     '''      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
''', '''      annotations:
        checksum/token: {{ .Values.registration.token | sha256sum }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
''', "probe_agent"),
    ("token-secret-outlives-release", "templates/secret.yaml",
     '''  {{- with include "probe-agent.annotations" (dict "ctx" .) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
''', '''  annotations:
    helm.sh/resource-policy: keep
''', "probe_agent"),
    ("default-token-shipped", "values.yaml",
     '  token: ""', '  token: "changeme"', "probe_agent"),
    ("no-token-allowed", "templates/_helpers.tpl",
     "{{- if and .Release.IsInstall (not (or $reg.token $reg.existingSecret)) -}}", "{{- if false -}}", "probe_agent"),
    ("upgrade-needs-a-token", "templates/_helpers.tpl",
     "{{- if and .Release.IsInstall (not (or $reg.token $reg.existingSecret)) -}}", "{{- if not (or $reg.token $reg.existingSecret) -}}", "probe_agent"),
    ("two-tokens-allowed", "templates/_helpers.tpl",
     "{{- if and $reg.token $reg.existingSecret -}}", "{{- if false -}}", "probe_agent"),
    ("one-time-token-for-many", "templates/_helpers.tpl",
     '{{- if and $reg.token (not (hasPrefix "ms_probe_" $reg.token)) (gt (.Values.replicaCount | int) 1) -}}', "{{- if false -}}", "probe_agent"),
    ("endpoint-may-be-plain", "templates/_helpers.tpl",
     '{{- if not (hasPrefix "https://" $reg.endpoint) -}}', "{{- if false -}}", "probe_agent"),
    ("ca-not-checked", "templates/_helpers.tpl",
     '{{- if not (hasPrefix "-----BEGIN CERTIFICATE-----" (b64dec $reg.caCertificateBase64)) -}}', "{{- if false -}}", "probe_agent"),
    ("browser-over-capacity", "templates/_helpers.tpl",
     "{{- if gt (.Values.browser.maxConcurrent | int) (.Values.maxConcurrent | int) -}}", "{{- if false -}}", "probe_agent"),
    ("registry-not-applied", "templates/_helpers.tpl",
     "{{- if $registry -}}\n{{- $parts", "{{- if false -}}\n{{- $parts", "probe_agent"),
    ("schema-allows-typos", "values.schema.json",
     '"type": "object",\n  "additionalProperties": false,\n  "$defs"', '"type": "object",\n  "additionalProperties": true,\n  "$defs"', "probe_agent"),
    ("image-drifts-from-annotation", "Chart.yaml",
     "image: ghcr.io/molesignal/probe-agent:latest", "image: ghcr.io/molesignal/probe-agent:stable", "probe_agent"),
    ("runs-as-root", "values.yaml",
     "  runAsUser: 10001", "  runAsUser: 0", "probe_agent"),
    ("may-escalate", "values.yaml",
     "  allowPrivilegeEscalation: false", "  allowPrivilegeEscalation: true", "probe_agent"),
    ("writable-root", "values.yaml",
     "  readOnlyRootFilesystem: true", "  readOnlyRootFilesystem: false", "probe_agent"),
    ("no-pings", "values.yaml",
     '''  sysctls:
    - name: net.ipv4.ping_group_range
      value: "0 2147483647"
''', "", "probe_agent"),
    ("service-account-token-mounted", "values.yaml",
     "  automount: false", "  automount: true", "probe_agent"),
    ("no-home-for-chromium", "templates/statefulset.yaml",
     "            - name: home\n              mountPath: /home/molesignal\n", "", "probe_agent"),
    ("no-tmp-for-chromium", "templates/statefulset.yaml",
     "            - name: tmp\n              mountPath: /tmp\n", "", "probe_agent"),
    ("flags-file-not-mounted", "templates/statefulset.yaml",
     "              mountPath: /etc/chromium.d/no-sandbox\n", "              mountPath: /etc/chromium/no-sandbox\n", "probe_agent"),
    ("flags-file-does-nothing", "templates/configmap.yaml",
     '    export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox"', '    export CHROMIUM_FLAGS="--no-sandbox"', "probe_agent"),
    ("sandbox-value-ignored", "templates/statefulset.yaml",
     "            {{- if not .Values.browser.sandbox }}\n            - name: chromium-flags", "            {{- if true }}\n            - name: chromium-flags", "probe_agent"),
    ("browser-slots-ignored", "templates/statefulset.yaml",
     "{{ .Values.browser.maxConcurrent | int | quote }}", '"1"', "probe_agent"),
    ("selector-gets-common-labels", "templates/_helpers.tpl",
     "app.kubernetes.io/component: agent\n{{- end -}}", "app.kubernetes.io/component: agent\n{{- with .Values.commonLabels }}\n{{ toYaml . }}\n{{- end }}\n{{- end -}}", "probe_agent"),
    ("test-passes-on-anything", "templates/tests/test-connection.yaml",
     '*) echo "the server did not answer: check registration.endpoint, the CA and [probe].server_names"; exit 1 ;;', '*) echo "the server did not answer" ;;', "probe_agent"),
    ("test-uses-the-real-token", "templates/tests/test-connection.yaml",
     "--token not-a-real-token", '--token "$REGISTER_TOKEN"', "probe_agent"),
    ("notes-forget-the-token-warning", "templates/_notes.tpl",
     "{{- if gt $replicas 1 }}", "{{- if false }}", "probe_agent"),
]

# The same for scripts/ci-install.sh: name, exact text, replacement. The scripts module covers them.
SCRIPT_MUTATIONS = [
    ("offline-agents-count", 'length == $count and all(.[]; .status == "online")', "length == $count"),
    ("shared-identity-unnoticed",
     '''  [[ "$(jq \'[.[].id] | unique | length\' <<<"${agents}")" == "${replicas}" ]] || { echo "Agents share an identity: ${agents}" >&2; return 1; }
''', ""),
    ("checks-not-run", '  run_checks "${location}" || return 1\n', ""),
    ("browser-check-not-run", '  run_check browser "${location}"', '  true browser "${location}"'),
    ("unhealthy-check-passes", '[[ "${outcome}" == "healthy" ]] && return 0', "return 0"),
    ("restart-not-tried", '  same_agent_after_restart "${full}" "${location}" "${agents}" "ci-${kind}" || return 1\n', ""),
    ("new-identity-unnoticed",
     '''        echo "the Agents changed: ${before} became ${agents}" >&2
        return 1
''', '''        echo "the Agents changed: ${before} became ${agents}" >&2
'''),
    ("heartbeat-not-awaited", '.status == "online" and .last_heartbeat_at > $stopped', '.status == "online"'),
    ("init-log-not-read",
     '''      kubectl --namespace "${agent_ns}" logs "${pod}" --container register | grep "already registered" \\
        || { echo "${pod} did not find its identity" >&2; return 1; }
''', ""),
    ("agent-failure-swallowed",
     '''        echo "::error title=probe-agent::${values} failed"
        status=1
''', '''        echo "::error title=probe-agent::${values} failed"
'''),
    ("secret-kept-for-the-restart", '  kubectl --namespace "${agent_ns}" delete secret "${secret}" || return 1\n', ""),
    ("no-evidence", '  [[ "${status}" == 0 ]] || dump "${agent_ns}"\n', ""),
    ("server-without-probe-values", ' --values "${probe_agent}/ci/molesignal-server.yaml"', ""),
    ("agents-not-pinned-to-the-tag",
     '--set "image.tag=${tag}" --set-string "registration.caCertificateBase64=${ca}"',
     '--set-string "registration.caCertificateBase64=${ca}"'),
    ("ca-not-passed", ' --set-string "registration.caCertificateBase64=${ca}"', ""),
    ("cookies-not-kept", '--cookie "${cookies}" --cookie-jar "${cookies}" ', ""),
    ("bearer-always-sent", '[[ -z "${session_token}" ]] || args+=(--header "Authorization: Bearer ${session_token}")', 'args+=(--header "Authorization: Bearer ${session_token}")'),
]


def run_tests(module: str, env_var: str, target: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "run.py"), "-k", module],
        env={**os.environ, env_var: str(target)},
        capture_output=True,
        text=True,
    )
    return re.findall(r"^\s+✗ (.+)$", proc.stdout, re.M)


def mutate_chart(chart: str, env_var: str, mutation: tuple[str, str, str, str, str]) -> bool:
    """True when a test noticed. A pattern that no longer matches counts as not noticed."""
    name, rel, old, new, module = mutation
    tmp = Path(tempfile.mkdtemp(prefix="mutant-"))
    try:
        copy = tmp / chart
        shutil.copytree(ROOT / "charts" / chart, copy)
        target = copy / rel
        text = target.read_text()
        if text.count(old) != 1:
            print(f"?? {name}: pattern found {text.count(old)} times in {rel}; update the mutation")
            return False
        target.write_text(text.replace(old, new))
        failed = run_tests(module, env_var, copy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        print(f"caught   {name:<38} ({len(failed)} failing: {failed[0][:70]})")
        return True
    print(f"SURVIVED {name:<38} no check failed in `{module}`")
    return False


def mutate_script(mutation: tuple[str, str, str]) -> bool:
    name, old, new = mutation
    tmp = Path(tempfile.mkdtemp(prefix="mutant-"))
    try:
        target = tmp / "ci-install.sh"
        text = (ROOT / "scripts" / "ci-install.sh").read_text()
        if text.count(old) != 1:
            print(f"?? {name}: pattern found {text.count(old)} times in the script; update the mutation")
            return False
        target.write_text(text.replace(old, new))
        target.chmod(0o755)
        failed = run_tests("scripts", "CI_INSTALL_SCRIPT", target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        print(f"caught   {name:<38} ({len(failed)} failing: {failed[0][:70]})")
        return True
    print(f"SURVIVED {name:<38} no check failed in `scripts`")
    return False


def main() -> int:
    pattern = sys.argv[sys.argv.index("-k") + 1] if "-k" in sys.argv else None
    survivors: list[str] = []
    ran = 0

    def wanted(mutation: tuple[str, ...]) -> bool:
        return not pattern or pattern in mutation[0]

    for mutation in filter(wanted, MUTATIONS):
        ran += 1
        if not mutate_chart("molesignal", "MOLESIGNAL_CHART", mutation):
            survivors.append(mutation[0])
    for mutation in filter(wanted, PROBE_AGENT_MUTATIONS):
        ran += 1
        if not mutate_chart("probe-agent", "PROBE_AGENT_CHART", mutation):
            survivors.append(mutation[0])
    for mutation in filter(wanted, SCRIPT_MUTATIONS):
        ran += 1
        if not mutate_script(mutation):
            survivors.append(mutation[0])
    print(f"\n{ran - len(survivors)} of {ran} mutations caught")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
