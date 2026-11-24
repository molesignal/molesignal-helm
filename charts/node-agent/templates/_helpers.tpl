{{/*
Names ------------------------------------------------------------------------
*/}}

{{- define "node-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Prefix of every resource name. Kept to 52 characters so that `-token` still fits in the 63
characters of a DNS label, and the pods of the DaemonSet keep the whole name before their suffix.
A release called `molesignal` gives `molesignal-node-agent`, the names of the agent's own manifest.
*/}}
{{- define "node-agent.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 52 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 52 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 52 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "node-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "node-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "node-agent.tokenSecretName" -}}
{{- if .Values.token.existingSecret -}}
{{- .Values.token.existingSecret -}}
{{- else -}}
{{- printf "%s-token" (include "node-agent.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "node-agent.priorityClassName" -}}
{{- if .Values.priorityClass.create -}}
{{- include "node-agent.fullname" . -}}
{{- else -}}
{{- .Values.priorityClassName -}}
{{- end -}}
{{- end -}}

{{/*
Labels -----------------------------------------------------------------------
*/}}

{{- define "node-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "node-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "node-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: agent
{{- end -}}

{{- define "node-agent.labels" -}}
helm.sh/chart: {{ include "node-agent.chart" . }}
{{ include "node-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: molesignal
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Annotations shared by every resource, merged with `extra` (a map, may be empty).
Prints nothing when there are none. dict "ctx" . "extra" (dict ...)
*/}}
{{- define "node-agent.annotations" -}}
{{- $all := merge (dict) (.extra | default dict) (.ctx.Values.commonAnnotations | default dict) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Images -----------------------------------------------------------------------
`global.imageRegistry` replaces the registry host (the first path element when it
looks like a host) and keeps the rest of the path, as in the other charts. The default
image has no host (Docker Hub), so the registry is put in front of all of it.
*/}}
{{- define "node-agent.image" -}}
{{- $repo := .Values.image.repository -}}
{{- $registry := trimSuffix "/" (default "" .Values.global.imageRegistry) -}}
{{- if $registry -}}
{{- $parts := splitList "/" $repo -}}
{{- $first := first $parts -}}
{{- if and (gt (len $parts) 1) (or (contains "." $first) (contains ":" $first) (eq $first "localhost")) -}}
{{- $repo = join "/" (rest $parts) -}}
{{- end -}}
{{- $repo = printf "%s/%s" $registry $repo -}}
{{- end -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- if .Values.image.digest -}}
{{- if $tag -}}
{{- printf "%s:%s@%s" $repo $tag .Values.image.digest -}}
{{- else -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- end -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/* Pull secrets as a YAML list of {name: ...}; prints nothing when there are none. */}}
{{- define "node-agent.imagePullSecrets" -}}
{{- $all := concat (.Values.global.imagePullSecrets | default list) (.Values.imagePullSecrets | default list) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Paths in the container ---------------------------------------------------------
Fixed: they are what the agent looks for, and what `MS_TOKEN_PATH` and `MS_STATE_DIR` name.
*/}}
{{- define "node-agent.tokenDir" -}}
/etc/molesignal-node-agent
{{- end -}}

{{- define "node-agent.stateDir" -}}
/var/lib/molesignal-node-agent
{{- end -}}

{{/*
The host mounts, in the order of the agent's manifest: value key, volume name, path in the
container, hostPath type. machineId has no `enabled`: it is always mounted.
*/}}
{{- define "node-agent.hostMounts" -}}
- {key: machineId, name: machine-id, mountPath: /etc/machine-id, type: File}
- {key: osRelease, name: os-release, mountPath: /etc/os-release, type: File}
- {key: btf, name: btf, mountPath: /sys/kernel/btf, type: ""}
- {key: tracefs, name: tracefs, mountPath: /sys/kernel/tracing, type: ""}
- {key: debugfs, name: debugfs, mountPath: /sys/kernel/debug, type: ""}
- {key: cgroup, name: cgroup, mountPath: /sys/fs/cgroup, type: Directory}
- {key: kmsg, name: kmsg, mountPath: /dev/kmsg, type: CharDevice}
- {key: dockerSocket, name: docker-sock, mountPath: /var/run/docker.sock, type: ""}
{{- end -}}

{{/* The host mounts that are switched on, as a JSON list: fromJsonArray (include ...). */}}
{{- define "node-agent.enabledHostMounts" -}}
{{- $out := list -}}
{{- range (include "node-agent.hostMounts" . | fromYamlArray) -}}
{{- $cfg := index $.Values.hostMounts .key -}}
{{- if or (eq .key "machineId") $cfg.enabled -}}
{{- $out = append $out (merge (dict "hostPath" $cfg.hostPath) .) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
Configuration ------------------------------------------------------------------
The `MS_*` variables of the ConfigMap, as a JSON map. A null value is left out, so that the
agent uses its default; an empty string is kept, because for some variables it means something
(`MS_DROPWATCH_FILTER=""` reports every drop, an empty device list watches no interface).
*/}}
{{- define "node-agent.configEnv" -}}
{{- $c := .Values.config -}}
{{- $env := dict "MS_ENDPOINT" .Values.endpoint -}}
{{- if not (kindIs "invalid" $c.logLevel) }}{{ $_ := set $env "MS_LOG_LEVEL" $c.logLevel }}{{ end -}}
{{- if not (kindIs "invalid" $c.logFormat) }}{{ $_ := set $env "MS_LOG_FORMAT" $c.logFormat }}{{ end -}}
{{- if not (kindIs "invalid" $c.healthAddr) }}{{ $_ := set $env "MS_HEALTH_ADDR" $c.healthAddr }}{{ end -}}
{{- if not (kindIs "invalid" $c.shutdownTimeoutMs) }}{{ $_ := set $env "MS_SHUTDOWN_TIMEOUT_MS" ($c.shutdownTimeoutMs | int64 | toString) }}{{ end -}}
{{- with $c.collectors.allow }}{{ $_ := set $env "MS_COLLECTORS_ALLOW" (join "," .) }}{{ end -}}
{{- with $c.collectors.deny }}{{ $_ := set $env "MS_COLLECTORS_DENY" (join "," .) }}{{ end -}}
{{- if not (kindIs "invalid" $c.dropwatchFilter) }}{{ $_ := set $env "MS_DROPWATCH_FILTER" $c.dropwatchFilter }}{{ end -}}
{{- range $key, $name := dict "eventsDevices" "MS_NETDEV_EVENTS_DEVICES" "hwDevices" "MS_NETDEV_HW_DEVICES" "dcbDevices" "MS_NETDEV_DCB_DEVICES" -}}
{{- $list := index $c.netdev $key -}}
{{- if not (kindIs "invalid" $list) }}{{ $_ := set $env $name (join "," $list) }}{{ end -}}
{{- end -}}
{{- if not (kindIs "invalid" $c.legacyMetrics) }}{{ $_ := set $env "MS_COMPATIBILITY_LEGACY_METRICS" ($c.legacyMetrics | toString) }}{{ end -}}
{{- range $k, $v := $c.extra }}{{ $_ := set $env $k ($v | toString) }}{{ end -}}
{{- toJson $env -}}
{{- end -}}

{{/* Variables the chart sets itself, which `config.extra` may not override. */}}
{{- define "node-agent.managedEnv" -}}
["MS_ENDPOINT", "MS_TOKEN_PATH", "MS_STATE_DIR", "MS_LOG_LEVEL", "MS_LOG_FORMAT", "MS_HEALTH_ADDR",
 "MS_SHUTDOWN_TIMEOUT_MS", "MS_COLLECTORS_ALLOW", "MS_COLLECTORS_DENY", "MS_DROPWATCH_FILTER",
 "MS_NETDEV_EVENTS_DEVICES", "MS_NETDEV_HW_DEVICES", "MS_NETDEV_DCB_DEVICES", "MS_COMPATIBILITY_LEGACY_METRICS"]
{{- end -}}

{{/*
Validation ---------------------------------------------------------------------
Fails the render early, with a message that says what to change.
*/}}
{{- define "node-agent.validate" -}}
{{- if .Values.enrollment.managed -}}
{{- fail "node-agent: `enrollment.managed` is reserved: the agent does not support managed enrollment yet, only standalone token mode. Set it to false and give a token." -}}
{{- end -}}
{{- if not .Values.endpoint -}}
{{- fail "node-agent: `endpoint` is required: the OTLP intake to send to, for example https://intake.molesignal.example." -}}
{{- end -}}
{{- if not (or (hasPrefix "https://" .Values.endpoint) (hasPrefix "http://" .Values.endpoint)) -}}
{{- fail (printf "node-agent: `endpoint` must start with http:// or https://. Got %q" .Values.endpoint) -}}
{{- end -}}
{{- $tok := .Values.token -}}
{{- if and $tok.value $tok.existingSecret -}}
{{- fail "node-agent: set either `token.value` or `token.existingSecret`, not both" -}}
{{- end -}}
{{- /* Unlike a Probe Agent, the node agent reads its token for as long as it runs: an upgrade needs one too. */ -}}
{{- if not (or $tok.value $tok.existingSecret) -}}
{{- fail "node-agent: no token. Set `token.existingSecret` (a Secret you manage, recommended) or `token.value`." -}}
{{- end -}}
{{- with .Values.podSecurityContext.fsGroup -}}
{{- fail "node-agent: `podSecurityContext.fsGroup` makes the kubelet give the token file to that group, readable, and the agent refuses a token that is not readable by its owner only. Leave fsGroup out." -}}
{{- end -}}
{{- $c := .Values.config -}}
{{- range $c.collectors.allow -}}
{{- if has . $c.collectors.deny -}}
{{- fail (printf "node-agent: collector %q is in both `config.collectors.allow` and `config.collectors.deny`" .) -}}
{{- end -}}
{{- end -}}
{{- range $key := list "eventsDevices" "hwDevices" "dcbDevices" -}}
{{- range (index $c.netdev $key | default list) -}}
{{- if contains "," . -}}
{{- fail (printf "node-agent: `config.netdev.%s` has %q: a pattern cannot contain a comma, because the agent splits the list on commas. Give each pattern as an item of its own." $key .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $managed := include "node-agent.managedEnv" . | fromJsonArray -}}
{{- range $k, $_ := $c.extra -}}
{{- if has $k $managed -}}
{{- fail (printf "node-agent: `config.extra` sets %s, which the chart sets itself: use the value of `config` (or `endpoint`) that writes it." $k) -}}
{{- end -}}
{{- end -}}
{{- $shutdown := ternary 10000 ($c.shutdownTimeoutMs | int64) (kindIs "invalid" $c.shutdownTimeoutMs) -}}
{{- if le (mul (.Values.terminationGracePeriodSeconds | int64) 1000) $shutdown -}}
{{- fail (printf "node-agent: `terminationGracePeriodSeconds` (%d s) has to be longer than the agent's shutdown window (%d ms): the pod would be killed while it still sends what it has." (.Values.terminationGracePeriodSeconds | int64) $shutdown) -}}
{{- end -}}
{{- end -}}
