{{- /* The text `helm install` prints. It lives in a named template so that the tests can render it. */ -}}
{{- define "probe-agent.notes" -}}
{{- $ns := .Release.Namespace -}}
{{- $full := include "probe-agent.fullname" . -}}
{{- $replicas := .Values.replicaCount | int -}}
Probe Agent {{ .Chart.AppVersion }} was installed as release "{{ .Release.Name }}", {{ $replicas }} {{ ternary "replica" "replicas" (eq $replicas 1) }}.

1. Each replica registers once with {{ .Values.registration.endpoint }}
   and keeps its identity on {{ if .Values.persistence.enabled }}its own volume{{ else }}an emptyDir{{ end }}. Watch that happen:

     kubectl --namespace {{ $ns }} logs {{ $full }}-0 --container register
     kubectl --namespace {{ $ns }} get pods --selector app.kubernetes.io/instance={{ .Release.Name }} --watch

2. Check that the endpoint answers and the CA is the right one:

     helm test {{ .Release.Name }} --namespace {{ $ns }}

3. The Agents appear online in MoleSignal under Synthetics > Agents.
{{- if gt $replicas 1 }}

{{ "!! " }}A one-time token registers one Agent, so the other replicas would stay in Init. With
   {{ $replicas }} replicas the token has to be an Agent Token.
{{- end }}
{{- if not .Values.persistence.enabled }}

{{ "!! " }}`persistence.enabled` is false: every new pod registers as a new Agent, the old ones
   stay behind as offline, and a one-time token only works once.
{{- end }}

Scaling down keeps the volume and the Agent: it comes back with the same identity when you scale up
again. To retire one, revoke it in MoleSignal and delete its volume, state-{{ $full }}-<ordinal>.
{{- end -}}
