{{- /* The text `helm install` prints. It lives in a named template so that the tests can render it. */ -}}
{{- define "node-agent.notes" -}}
{{- $ns := .Release.Namespace -}}
{{- $full := include "node-agent.fullname" . -}}
MoleSignal node agent {{ .Chart.AppVersion }} was installed as release "{{ .Release.Name }}": one pod per
node, sending to {{ .Values.endpoint }}.
{{- if not .Values.namespace.create }}

{{ "!! " }}The agent needs the `privileged` Pod Security level: `baseline` already refuses host
   namespaces, host paths and added capabilities. If the pods do not appear, label the namespace:

     kubectl label namespace {{ $ns }} pod-security.kubernetes.io/enforce=privileged
{{- end }}

1. Watch the pods start, one per node:

     kubectl --namespace {{ $ns }} rollout status daemonset/{{ $full }}
     kubectl --namespace {{ $ns }} get pods --selector app.kubernetes.io/instance={{ .Release.Name }} --output wide

2. Ask an agent what it can and cannot do on its node (kernel, BTF, permissions, mounts, cgroups,
   container runtime, the endpoint and the token):

     kubectl --namespace {{ $ns }} exec daemonset/{{ $full }} -- molesignal-node-agent doctor

   A collector whose mount is switched off, or whose kernel support is missing, shows as degraded.
{{- $off := list -}}
{{- range $k, $v := .Values.hostMounts -}}
{{- if and (hasKey $v "enabled") (not $v.enabled) -}}
{{- $off = append $off $k -}}
{{- end -}}
{{- end -}}
{{- if $off }}
   Switched off here: {{ $off | sortAlpha | join ", " }}.
{{- end }}
{{- end -}}
