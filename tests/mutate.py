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
CHART = ROOT / "charts" / "molesignal"

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


def main() -> int:
    pattern = sys.argv[sys.argv.index("-k") + 1] if "-k" in sys.argv else None
    survivors: list[str] = []
    ran = 0
    for name, rel, old, new, module in MUTATIONS:
        if pattern and pattern not in name:
            continue
        ran += 1
        tmp = Path(tempfile.mkdtemp(prefix="mutant-"))
        try:
            copy = tmp / "molesignal"
            shutil.copytree(CHART, copy)
            target = copy / rel
            text = target.read_text()
            if text.count(old) != 1:
                print(f"?? {name}: pattern found {text.count(old)} times in {rel}; update the mutation")
                survivors.append(name)
                continue
            target.write_text(text.replace(old, new))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "run.py"), "-k", module],
                env={**os.environ, "MOLESIGNAL_CHART": str(copy)},
                capture_output=True,
                text=True,
            )
            failed = re.findall(r"^\s+✗ (.+)$", proc.stdout, re.M)
            if failed:
                print(f"caught   {name:<38} ({len(failed)} failing: {failed[0][:70]})")
            else:
                print(f"SURVIVED {name:<38} no check failed in `{module}`")
                survivors.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{ran - len(survivors)} of {ran} mutations caught")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
