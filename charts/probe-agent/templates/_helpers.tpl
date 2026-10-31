{{/*
Names ------------------------------------------------------------------------
*/}}

{{- define "probe-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Prefix of every resource name. Kept to 43 characters so that the pods of the StatefulSet,
the claims of its volumes and `-registration` still fit in the 63 characters Kubernetes allows.
*/}}
{{- define "probe-agent.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 43 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 43 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 43 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "probe-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "probe-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "probe-agent.tokenSecretName" -}}
{{- if .Values.registration.existingSecret -}}
{{- .Values.registration.existingSecret -}}
{{- else -}}
{{- printf "%s-registration" (include "probe-agent.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Labels -----------------------------------------------------------------------
*/}}

{{- define "probe-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "probe-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "probe-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: agent
{{- end -}}

{{- define "probe-agent.labels" -}}
helm.sh/chart: {{ include "probe-agent.chart" . }}
{{ include "probe-agent.selectorLabels" . }}
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
{{- define "probe-agent.annotations" -}}
{{- $all := merge (dict) (.extra | default dict) (.ctx.Values.commonAnnotations | default dict) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Images -----------------------------------------------------------------------
`global.imageRegistry` replaces the registry host (the first path element when it
looks like a host) and keeps the rest of the path, as in the molesignal chart.
*/}}
{{- define "probe-agent.image" -}}
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
{{- define "probe-agent.imagePullSecrets" -}}
{{- $all := concat (.Values.global.imagePullSecrets | default list) (.Values.imagePullSecrets | default list) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Where the identity lives. A subdirectory of the volume, because `probe-agent register` makes
its state directory private with chmod 0700: the root of a volume belongs to root, and a
process that runs as 10001 may not change its mode. A fresh volume can hold `lost+found` too.
*/}}
{{- define "probe-agent.volumeMountPath" -}}
/var/lib/molesignal-probe
{{- end -}}

{{- define "probe-agent.stateDir" -}}
{{- include "probe-agent.volumeMountPath" . }}/state
{{- end -}}

{{/*
Validation ---------------------------------------------------------------------
Fails the render early, with a message that says what to change.
*/}}
{{- define "probe-agent.validate" -}}
{{- $reg := .Values.registration -}}
{{- if not $reg.endpoint -}}
{{- fail "probe-agent: `registration.endpoint` is required: the address of the server's register port, for example https://molesignal.example.com:5084. It has to be one of the `[probe].server_names` of the server." -}}
{{- end -}}
{{- if not (hasPrefix "https://" $reg.endpoint) -}}
{{- fail (printf "probe-agent: `registration.endpoint` must start with https://: the register port only speaks TLS. Got %q" $reg.endpoint) -}}
{{- end -}}
{{- if not $reg.caCertificateBase64 -}}
{{- fail "probe-agent: `registration.caCertificateBase64` is required: the base64 of the server's Probe CA, which the register command prints as --ca-certificate-base64." -}}
{{- end -}}
{{- if not (hasPrefix "-----BEGIN CERTIFICATE-----" (b64dec $reg.caCertificateBase64)) -}}
{{- fail "probe-agent: `registration.caCertificateBase64` is not the base64 of a PEM certificate. Copy the value of --ca-certificate-base64 from the register command." -}}
{{- end -}}
{{- if and $reg.token $reg.existingSecret -}}
{{- fail "probe-agent: set either `registration.token` or `registration.existingSecret`, not both" -}}
{{- end -}}
{{- /* An upgrade may leave both out: Agents that have an identity no longer need a token. */ -}}
{{- if and .Release.IsInstall (not (or $reg.token $reg.existingSecret)) -}}
{{- fail "probe-agent: no registration token. Create one for an Agent Pool location under Synthetics, then set `registration.existingSecret` (a Secret you manage, recommended) or `registration.token`." -}}
{{- end -}}
{{- /* Only an inline token can be told apart; an existing Secret is not readable from here. */ -}}
{{- if and $reg.token (not (hasPrefix "ms_probe_" $reg.token)) (gt (.Values.replicaCount | int) 1) -}}
{{- fail "probe-agent: `registration.token` looks like a one-time token, which registers a single Agent, and `replicaCount` is above 1. Create an Agent Token (it starts with ms_probe_), which every replica can register with." -}}
{{- end -}}
{{- if gt (.Values.browser.maxConcurrent | int) (.Values.maxConcurrent | int) -}}
{{- fail "probe-agent: `browser.maxConcurrent` is above `maxConcurrent`: a browser check also takes one of the Agent's general slots" -}}
{{- end -}}
{{- end -}}
