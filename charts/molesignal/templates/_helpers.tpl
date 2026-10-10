{{/*
Names ------------------------------------------------------------------------
*/}}

{{- define "molesignal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Prefix of every resource name. Kept to 43 characters so that suffixes such as
`-postgresql-headless` still fit in the 63 characters Kubernetes allows.
*/}}
{{- define "molesignal.fullname" -}}
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

{{/* <fullname>-<component>. dict "ctx" . "component" "router" */}}
{{- define "molesignal.componentName" -}}
{{- printf "%s-%s" (include "molesignal.fullname" .ctx) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Name of a role's workload and of its Service. The standalone StatefulSet and its
Service are simply `<fullname>`. dict "ctx" . "role" "router"
*/}}
{{- define "molesignal.workloadName" -}}
{{- if eq .role "standalone" -}}
{{- include "molesignal.fullname" .ctx -}}
{{- else -}}
{{- include "molesignal.componentName" (dict "ctx" .ctx "component" .role) -}}
{{- end -}}
{{- end -}}

{{/*
The Service that fronts the UI and HTTP API: the standalone Service, or the router.
*/}}
{{- define "molesignal.frontService" -}}
{{- if eq .Values.mode "standalone" -}}
{{- include "molesignal.fullname" . -}}
{{- else -}}
{{- include "molesignal.componentName" (dict "ctx" . "component" "router") -}}
{{- end -}}
{{- end -}}

{{/* The Service that receives OTLP gRPC. */}}
{{- define "molesignal.otlpService" -}}
{{- include "molesignal.frontService" . -}}
{{- end -}}

{{- define "molesignal.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "molesignal.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Labels -----------------------------------------------------------------------
*/}}

{{- define "molesignal.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* dict "ctx" . "component" "router" */}}
{{- define "molesignal.selectorLabels" -}}
app.kubernetes.io/name: {{ include "molesignal.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* dict "ctx" . "component" "router" ["version" "17"] */}}
{{- define "molesignal.labels" -}}
helm.sh/chart: {{ include "molesignal.chart" .ctx }}
{{ include "molesignal.selectorLabels" . }}
app.kubernetes.io/version: {{ (default .ctx.Chart.AppVersion .version) | quote }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
app.kubernetes.io/part-of: molesignal
{{- with .ctx.Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Annotations shared by every resource, merged with `extra` (a map, may be empty).
Prints nothing when there are none. dict "ctx" . "extra" (dict ...)
*/}}
{{- define "molesignal.annotations" -}}
{{- $all := merge (dict) (.extra | default dict) (.ctx.Values.commonAnnotations | default dict) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Images -----------------------------------------------------------------------
dict "ctx" . "image" <repository/tag/digest map> "defaultTag" <tag when empty>
`global.imageRegistry` replaces the registry host (the first path element when
it looks like a host) and keeps the rest of the path.
*/}}
{{- define "molesignal.imageRef" -}}
{{- $repo := .image.repository -}}
{{- $registry := trimSuffix "/" (default "" .ctx.Values.global.imageRegistry) -}}
{{- if $registry -}}
{{- $parts := splitList "/" $repo -}}
{{- $first := first $parts -}}
{{- if and (gt (len $parts) 1) (or (contains "." $first) (contains ":" $first) (eq $first "localhost")) -}}
{{- $repo = join "/" (rest $parts) -}}
{{- end -}}
{{- $repo = printf "%s/%s" $registry $repo -}}
{{- end -}}
{{- $tag := default (default "" .defaultTag) .image.tag -}}
{{- if .image.digest -}}
{{- if $tag -}}
{{- printf "%s:%s@%s" $repo $tag .image.digest -}}
{{- else -}}
{{- printf "%s@%s" $repo .image.digest -}}
{{- end -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/* The MoleSignal application image. */}}
{{- define "molesignal.image" -}}
{{- include "molesignal.imageRef" (dict "ctx" . "image" .Values.image "defaultTag" .Chart.AppVersion) -}}
{{- end -}}

{{/* Pull secrets as a YAML list of {name: ...}; prints nothing when there are none. */}}
{{- define "molesignal.imagePullSecrets" -}}
{{- $all := concat (.Values.global.imagePullSecrets | default list) (.Values.imagePullSecrets | default list) -}}
{{- if $all -}}
{{- toYaml $all -}}
{{- end -}}
{{- end -}}

{{/*
Roles ------------------------------------------------------------------------
Static facts about each role: workload kind, whether it keeps a write-ahead log,
whether it advertises a gRPC address to its peers, the ports it listens on and its
readiness probe. They mirror deploy/k8s in the application repository.
*/}}
{{- define "molesignal.roleSpecs" -}}
standalone:
  kind: StatefulSet
  wal: true
  advertise: false
  service: true
  ports: [http, grpc, otlp-grpc, probe-register, probe-control]
  probe: {path: /api/v1/readyz, initialDelaySeconds: 10, periodSeconds: 5, failureThreshold: 12}
router:
  kind: Deployment
  wal: false
  advertise: false
  service: true
  ports: [http, otlp-grpc]
  probe: {path: /api/v1/healthz, initialDelaySeconds: 5, periodSeconds: 10}
intake:
  kind: StatefulSet
  wal: true
  advertise: true
  service: true
  headless: true
  ports: [http, grpc, otlp-grpc]
  probe: {path: /api/v1/readyz, initialDelaySeconds: 10, periodSeconds: 5, failureThreshold: 12}
querier:
  kind: Deployment
  wal: false
  advertise: true
  service: true
  ports: [http, grpc]
  probe: {path: /api/v1/readyz, initialDelaySeconds: 5, periodSeconds: 10}
compactor:
  kind: Deployment
  wal: false
  advertise: true
  service: false
  ports: [http]
  probe: {path: /api/v1/readyz, initialDelaySeconds: 5, periodSeconds: 10}
alert:
  kind: Deployment
  wal: false
  advertise: true
  service: true
  ports: [http, probe-register, probe-control]
  probe: {path: /api/v1/readyz, initialDelaySeconds: 5, periodSeconds: 10}
{{- end -}}

{{/* JSON list of the roles that run in the selected mode. */}}
{{- define "molesignal.activeRoles" -}}
{{- if eq .Values.mode "standalone" -}}
["standalone"]
{{- else -}}
["router","intake","querier","compactor","alert"]
{{- end -}}
{{- end -}}

{{/*
Number of a named port, honouring overrides in `config`. dict "ctx" . "name" "http"
*/}}
{{- define "molesignal.port" -}}
{{- $cfg := .ctx.Values.config | default dict -}}
{{- if eq .name "http" -}}
{{- dig "http" "port" 5080 $cfg | int -}}
{{- else if eq .name "grpc" -}}
{{- dig "grpc" "port" 5082 $cfg | int -}}
{{- else if eq .name "otlp-grpc" -}}
{{- dig "otlp_grpc" "port" 4317 $cfg | int -}}
{{- else if eq .name "probe-register" -}}
{{- dig "probe" "register_port" 5084 $cfg | int -}}
{{- else if eq .name "probe-control" -}}
{{- dig "probe" "control_port" 5085 $cfg | int -}}
{{- else -}}
{{- fail (printf "molesignal: unknown port %q" .name) -}}
{{- end -}}
{{- end -}}

{{/*
Configuration ------------------------------------------------------------------
Everything the chart knows that belongs in config.toml. `.Values.config` is merged
over it, so a user value always wins. Secrets never go here; they are environment
variables.
*/}}

{{/* Base URL the headless browser uses to render scheduled reports. */}}
{{- define "molesignal.rendererBaseUrl" -}}
{{- if .Values.web.enabled -}}
{{- printf "http://%s:%d" (include "molesignal.componentName" (dict "ctx" . "component" "web")) (.Values.web.service.port | int) -}}
{{- else -}}
{{- printf "http://%s:%d" (include "molesignal.frontService" .) (include "molesignal.port" (dict "ctx" . "name" "http") | int) -}}
{{- end -}}
{{- end -}}

{{/* Public URL of the UI, or empty. */}}
{{- define "molesignal.externalUrl" -}}
{{- if .Values.externalUrl -}}
{{- .Values.externalUrl -}}
{{- else if and .Values.ingress.enabled .Values.ingress.hosts -}}
{{- $host := (first .Values.ingress.hosts).host -}}
{{- printf "%s://%s" (ternary "https" "http" (not (empty .Values.ingress.tls))) $host -}}
{{- end -}}
{{- end -}}

{{/* The chart-derived part of config.toml, as JSON. */}}
{{- define "molesignal.config.base" -}}
{{- $objectStore := dict "backend" "s3" -}}
{{- if .Values.rustfs.enabled -}}
{{- $_ := set $objectStore "bucket" .Values.objectStore.bucket -}}
{{- $_ := set $objectStore "region" "us-east-1" -}}
{{- $_ := set $objectStore "endpoint" (printf "http://%s:9000" (include "molesignal.componentName" (dict "ctx" . "component" "rustfs"))) -}}
{{- $_ := set $objectStore "path_style" true -}}
{{- else -}}
{{- $_ := set $objectStore "bucket" .Values.objectStore.bucket -}}
{{- $_ := set $objectStore "region" .Values.objectStore.region -}}
{{- $_ := set $objectStore "path_style" (.Values.objectStore.pathStyle | default false) -}}
{{- if .Values.objectStore.endpoint -}}
{{- $_ := set $objectStore "endpoint" .Values.objectStore.endpoint -}}
{{- end -}}
{{- end -}}
{{- $base := dict
    "wal" (dict "dir" "/data/wal")
    "telemetry" (dict "log_level" .Values.logging.level "log_format" .Values.logging.format)
    "object_store" $objectStore
    "scheduled_reports" (dict "renderer" (dict "base_url" (include "molesignal.rendererBaseUrl" .))) -}}
{{- $url := include "molesignal.externalUrl" . -}}
{{- if $url -}}
{{- $_ := set $base "http" (dict "external_url" $url) -}}
{{- end -}}
{{- toJson $base -}}
{{- end -}}

{{/*
Deep merge of two maps, as JSON. `over` wins; a map merges key by key, anything
else (including false, 0 and "") replaces, and null removes the key.
dict "base" <map> "over" <map>
*/}}
{{- define "molesignal.merge" -}}
{{- $out := deepCopy .base -}}
{{- range $k, $v := .over -}}
{{- if kindIs "invalid" $v -}}
{{- $_ := unset $out $k -}}
{{- else if and (kindIs "map" $v) (kindIs "map" (get $out $k)) -}}
{{- $_ := set $out $k (include "molesignal.merge" (dict "base" (get $out $k) "over" $v) | fromJson) -}}
{{- else -}}
{{- $_ := set $out $k (deepCopy $v) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* The complete settings tree, as JSON. */}}
{{- define "molesignal.config.effective" -}}
{{- include "molesignal.merge" (dict "base" (include "molesignal.config.base" . | fromJson) "over" (.Values.config | default dict)) -}}
{{- end -}}

{{/*
TOML ---------------------------------------------------------------------------
Helm's `toToml` writes whole numbers read from a values file as floats (86400.0),
which the application's integer settings reject, so the document is written here.
Supported: nested tables, strings, booleans, numbers and arrays of those. Arrays of
tables are rejected.
*/}}

{{- define "molesignal.toml.key" -}}
{{- if regexMatch "^[A-Za-z0-9_-]+$" . -}}{{ . }}{{- else -}}{{ toJson . }}{{- end -}}
{{- end -}}

{{- define "molesignal.toml.value" -}}
{{- if kindIs "string" . -}}
{{- toJson . -}}
{{- else if kindIs "bool" . -}}
{{- . -}}
{{- else if kindIs "slice" . -}}
{{- range . -}}
{{- if or (kindIs "map" .) (kindIs "slice" .) -}}
{{- fail "molesignal: `config` cannot contain arrays of tables or nested arrays; set those with extraEnv (MS_<SECTION>_<KEY>=<JSON>) instead" -}}
{{- end -}}
{{- end -}}
{{- toJson . -}}
{{- else if kindIs "float64" . -}}
{{- if eq (floor .) . -}}{{ int64 . }}{{- else -}}{{ . }}{{- end -}}
{{- else if or (kindIs "int" .) (kindIs "int64" .) -}}
{{- . -}}
{{- else -}}
{{- fail (printf "molesignal: unsupported value of type %s in `config`" (kindOf .)) -}}
{{- end -}}
{{- end -}}

{{/* A JSON array of lines. dict "path" (list "a" "b") "map" <map> */}}
{{- define "molesignal.toml.lines" -}}
{{- $lines := list -}}
{{- $path := .path -}}
{{- $hasScalar := false -}}
{{- range $k, $v := .map -}}
{{- if not (or (kindIs "map" $v) (kindIs "invalid" $v)) -}}
{{- $hasScalar = true -}}
{{- end -}}
{{- end -}}
{{- if and $path $hasScalar -}}
{{- $quoted := list -}}
{{- range $path -}}
{{- $quoted = append $quoted (include "molesignal.toml.key" .) -}}
{{- end -}}
{{- $lines = append $lines "" -}}
{{- $lines = append $lines (printf "[%s]" (join "." $quoted)) -}}
{{- end -}}
{{- range $k, $v := .map -}}
{{- if not (or (kindIs "map" $v) (kindIs "invalid" $v)) -}}
{{- $lines = append $lines (printf "%s = %s" (include "molesignal.toml.key" $k) (include "molesignal.toml.value" $v)) -}}
{{- end -}}
{{- end -}}
{{- range $k, $v := .map -}}
{{- if kindIs "map" $v -}}
{{- $lines = concat $lines (include "molesignal.toml.lines" (dict "path" (append $path $k) "map" $v) | fromJsonArray) -}}
{{- end -}}
{{- end -}}
{{- toJson $lines -}}
{{- end -}}

{{/* config.toml */}}
{{- define "molesignal.config.toml" -}}
{{- $tree := include "molesignal.config.effective" . | fromJson -}}
{{- $lines := include "molesignal.toml.lines" (dict "path" (list) "map" $tree) | fromJsonArray -}}
{{- printf "# Generated by the molesignal Helm chart. Change it through the `config` value.\n%s\n" (join "\n" $lines) -}}
{{- end -}}

{{/*
Secrets ------------------------------------------------------------------------
*/}}

{{- define "molesignal.secretName" -}}
{{- default (printf "%s-secret" (include "molesignal.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Generated Secret for inline credentials of an external database / object store. */}}
{{- define "molesignal.externalSecretName" -}}
{{- printf "%s-external" (include "molesignal.fullname" .) -}}
{{- end -}}

{{- define "molesignal.postgresql.secretName" -}}
{{- default (include "molesignal.componentName" (dict "ctx" . "component" "postgresql")) .Values.postgresql.auth.existingSecret -}}
{{- end -}}

{{- define "molesignal.rustfs.secretName" -}}
{{- default (include "molesignal.componentName" (dict "ctx" . "component" "rustfs")) .Values.rustfs.auth.existingSecret -}}
{{- end -}}

{{/*
The base64 value to store for one Secret key: the explicit value when there is one,
else what the Secret already holds in the cluster (so a generated value survives an
upgrade), else the freshly generated one.
dict "ctx" . "name" <secret> "key" <key> "value" <explicit, raw> "generate" <raw>
*/}}
{{- define "molesignal.secretData" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace .name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key -}}
{{- else -}}
{{- .generate | b64enc -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
A fingerprint of the secret values the user supplied. Pods carry it as an annotation
so that changing one of them rolls the workload. Generated values are left out on
purpose: they differ on every render and must not restart anything.
*/}}
{{- define "molesignal.secretChecksum" -}}
{{- dict
    "cipherKey" .Values.secrets.cipherKey
    "clusterToken" .Values.secrets.clusterToken
    "jwtSecretOverride" .Values.secrets.jwtSecretOverride
    "dsn" .Values.externalDatabase.dsn
    "accessKey" .Values.objectStore.accessKey
    "secretKey" .Values.objectStore.secretKey
    "pgPassword" .Values.postgresql.auth.password
    "rustfsAccessKey" .Values.rustfs.auth.accessKey
    "rustfsSecretKey" .Values.rustfs.auth.secretKey
  | toJson | sha256sum -}}
{{- end -}}

{{/*
Database -----------------------------------------------------------------------
*/}}

{{/* Environment entries that provide the metadata database connection string. */}}
{{- define "molesignal.env.database" -}}
{{- if .Values.postgresql.enabled -}}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "molesignal.postgresql.secretName" . }}
      key: {{ .Values.postgresql.auth.secretKey }}
- name: MS_META_DATABASE_DSN
  value: {{ printf "postgres://%s:$(DB_PASSWORD)@%s:5432/%s" .Values.postgresql.auth.username (include "molesignal.componentName" (dict "ctx" . "component" "postgresql")) .Values.postgresql.auth.database | quote }}
{{- else if .Values.externalDatabase.existingSecret -}}
- name: MS_META_DATABASE_DSN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalDatabase.existingSecret }}
      key: {{ .Values.externalDatabase.existingSecretKey }}
{{- else -}}
- name: MS_META_DATABASE_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "molesignal.externalSecretName" . }}
      key: database-dsn
{{- end -}}
{{- end -}}

{{/*
Object store ---------------------------------------------------------------------
*/}}

{{/* Where the object-store credentials live, as JSON {"name","accessKey","secretKey"}, or "". */}}
{{- define "molesignal.objectStore.credentials" -}}
{{- if .Values.rustfs.enabled -}}
{{- dict "name" (include "molesignal.rustfs.secretName" .) "accessKey" .Values.rustfs.auth.secretKeys.accessKey "secretKey" .Values.rustfs.auth.secretKeys.secretKey | toJson -}}
{{- else if .Values.objectStore.existingSecret -}}
{{- dict "name" .Values.objectStore.existingSecret "accessKey" .Values.objectStore.existingSecretKeys.accessKey "secretKey" .Values.objectStore.existingSecretKeys.secretKey | toJson -}}
{{- else if and .Values.objectStore.accessKey .Values.objectStore.secretKey -}}
{{- dict "name" (include "molesignal.externalSecretName" .) "accessKey" "object-store-access-key" "secretKey" "object-store-secret-key" | toJson -}}
{{- end -}}
{{- end -}}

{{/*
Pods ---------------------------------------------------------------------------
*/}}

{{/* Environment of a MoleSignal container. dict "ctx" . "role" "router" "spec" <role spec> */}}
{{- define "molesignal.env" -}}
{{- $ctx := .ctx -}}
{{- $roleCfg := index $ctx.Values .role -}}
- name: RELEASE_CHANNEL
  value: {{ $ctx.Values.releaseChannel | quote }}
- name: MS_NODE_ROLES
  value: {{ printf "[%q]" .role | quote }}
{{- if .spec.advertise }}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: MS_CLUSTER_ADVERTISE_ADDR
  value: {{ printf "$(POD_IP):%d" (include "molesignal.port" (dict "ctx" $ctx "name" "grpc") | int) | quote }}
{{- end }}
- name: MS_CIPHER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "molesignal.secretName" $ctx }}
      key: {{ $ctx.Values.secrets.keys.cipherKey }}
- name: MS_SELF_TELEMETRY_CLUSTER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "molesignal.secretName" $ctx }}
      key: {{ $ctx.Values.secrets.keys.clusterToken }}
- name: MS_AUTH_JWT_SECRET_OVERRIDE
  valueFrom:
    secretKeyRef:
      name: {{ include "molesignal.secretName" $ctx }}
      key: {{ $ctx.Values.secrets.keys.jwtSecretOverride }}
      optional: true
{{ include "molesignal.env.database" $ctx }}
{{- $credsJson := include "molesignal.objectStore.credentials" $ctx }}
{{- if $credsJson }}
{{- $creds := fromJson $credsJson }}
- name: MS_OBJECT_STORE_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $creds.name }}
      key: {{ $creds.accessKey }}
- name: MS_OBJECT_STORE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $creds.name }}
      key: {{ $creds.secretKey }}
{{- end }}
{{- with $ctx.Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- with $roleCfg.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Readiness probe of a role: the built-in one, overlaid with `<role>.readinessProbe`.
An override that names its own handler (httpGet, tcpSocket, exec, grpc) replaces the
whole probe.
*/}}
{{- define "molesignal.probe.readiness" -}}
{{- $roleCfg := index .ctx.Values .role -}}
{{- $override := $roleCfg.readinessProbe | default dict -}}
{{- $p := .spec.probe -}}
{{- $probe := dict "httpGet" (dict "path" $p.path "port" "http") "initialDelaySeconds" ($p.initialDelaySeconds | int) "periodSeconds" ($p.periodSeconds | int) -}}
{{- if $p.failureThreshold -}}
{{- $_ := set $probe "failureThreshold" ($p.failureThreshold | int) -}}
{{- end -}}
{{- if or (hasKey $override "httpGet") (hasKey $override "tcpSocket") (hasKey $override "exec") (hasKey $override "grpc") -}}
{{- toYaml $override -}}
{{- else -}}
{{- toYaml (mergeOverwrite $probe $override) -}}
{{- end -}}
{{- end -}}

{{/*
Init container that holds the pod back until the bundled PostgreSQL and RustFS
answer. The application image already carries bash, curl and coreutils.
*/}}
{{- define "molesignal.initContainer.wait" -}}
{{- $pg := include "molesignal.componentName" (dict "ctx" . "component" "postgresql") -}}
{{- $s3 := include "molesignal.componentName" (dict "ctx" . "component" "rustfs") -}}
- name: wait-for-dependencies
  image: {{ include "molesignal.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: ["bash", "-ec"]
  args:
    - |
      wait_tcp() {
        until timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; do
          echo "waiting for $1:$2"; sleep 2
        done
      }
      wait_http() {
        until curl -fsS --max-time 3 -o /dev/null "$1"; do
          echo "waiting for $1"; sleep 2
        done
      }
      {{- if .Values.postgresql.enabled }}
      wait_tcp {{ $pg }} 5432
      {{- end }}
      {{- if .Values.rustfs.enabled }}
      wait_http http://{{ $s3 }}:9000/health
      {{- end }}
      echo "dependencies are ready"
  {{- with .Values.waitForDependencies.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}

{{/*
The pod template of a role, shared by Deployments and StatefulSets.
dict "ctx" . "role" "router"
*/}}
{{- define "molesignal.podTemplate" -}}
{{- $ctx := .ctx -}}
{{- $role := .role -}}
{{- $roleCfg := index $ctx.Values $role -}}
{{- $spec := index (include "molesignal.roleSpecs" $ctx | fromYaml) $role -}}
{{- $annotations := merge (dict) ($roleCfg.podAnnotations | default dict) ($ctx.Values.podAnnotations | default dict) (dict "checksum/config" (include "molesignal.config.toml" $ctx | sha256sum)) -}}
{{- $_ := set $annotations "checksum/secret" (include "molesignal.secretChecksum" $ctx) -}}
{{- $securityContext := or $roleCfg.securityContext $ctx.Values.securityContext -}}
{{- $podSecurityContext := or $roleCfg.podSecurityContext $ctx.Values.podSecurityContext -}}
{{- $priorityClassName := or $roleCfg.priorityClassName $ctx.Values.priorityClassName -}}
{{- $nodeSelector := or $roleCfg.nodeSelector $ctx.Values.nodeSelector -}}
{{- $affinity := or $roleCfg.affinity $ctx.Values.affinity -}}
{{- $tolerations := or $roleCfg.tolerations $ctx.Values.tolerations -}}
{{- $spread := or $roleCfg.topologySpreadConstraints $ctx.Values.topologySpreadConstraints -}}
{{- $waits := or $ctx.Values.postgresql.enabled $ctx.Values.rustfs.enabled -}}
metadata:
  labels:
    {{- include "molesignal.labels" (dict "ctx" $ctx "component" $role) | nindent 4 }}
    {{- with merge (dict) ($roleCfg.podLabels | default dict) ($ctx.Values.podLabels | default dict) }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  annotations:
    {{- toYaml $annotations | nindent 4 }}
spec:
  serviceAccountName: {{ include "molesignal.serviceAccountName" $ctx }}
  automountServiceAccountToken: {{ $ctx.Values.serviceAccount.automount }}
  {{- with include "molesignal.imagePullSecrets" $ctx }}
  imagePullSecrets:
    {{- . | nindent 4 }}
  {{- end }}
  {{- with $podSecurityContext }}
  securityContext:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $roleCfg.terminationGracePeriodSeconds }}
  terminationGracePeriodSeconds: {{ . }}
  {{- end }}
  {{- with $priorityClassName }}
  priorityClassName: {{ . }}
  {{- end }}
  {{- if and $waits $ctx.Values.waitForDependencies.enabled }}
  initContainers:
    {{- include "molesignal.initContainer.wait" $ctx | nindent 4 }}
  {{- end }}
  containers:
    - name: molesignal
      image: {{ include "molesignal.image" $ctx }}
      imagePullPolicy: {{ $ctx.Values.image.pullPolicy }}
      args: ["--config", "/app/conf/config.toml"]
      env:
        {{- include "molesignal.env" (dict "ctx" $ctx "role" $role "spec" $spec) | nindent 8 }}
      {{- with $ctx.Values.extraEnvFrom }}
      envFrom:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      ports:
        {{- range $spec.ports }}
        - name: {{ . }}
          containerPort: {{ include "molesignal.port" (dict "ctx" $ctx "name" .) }}
        {{- end }}
      readinessProbe:
        {{- include "molesignal.probe.readiness" (dict "ctx" $ctx "role" $role "spec" $spec) | nindent 8 }}
      {{- with $roleCfg.livenessProbe }}
      livenessProbe:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $roleCfg.startupProbe }}
      startupProbe:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $roleCfg.resources }}
      resources:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $securityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      volumeMounts:
        - name: config
          mountPath: /app/conf
        {{- if $spec.wal }}
        - name: data
          mountPath: /data
        {{- end }}
        {{- with $ctx.Values.extraVolumeMounts }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
  {{- with $nodeSelector }}
  nodeSelector:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $affinity }}
  affinity:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $tolerations }}
  tolerations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $spread }}
  topologySpreadConstraints:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  volumes:
    - name: config
      configMap:
        name: {{ include "molesignal.fullname" $ctx }}-config
    {{- if and $spec.wal (not $roleCfg.persistence.enabled) }}
    - name: data
      emptyDir: {}
    {{- end }}
    {{- with $ctx.Values.extraVolumes }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end -}}

{{/*
Validation ---------------------------------------------------------------------
Fails the render early, with a message that says what to change.
*/}}
{{- define "molesignal.validate" -}}
{{- if not (has .Values.mode (list "standalone" "distributed")) -}}
{{- fail (printf "molesignal: `mode` must be \"standalone\" or \"distributed\", got %q" .Values.mode) -}}
{{- end -}}
{{- if not (has .Values.releaseChannel (list "alpha" "beta" "rc" "stable")) -}}
{{- fail (printf "molesignal: `releaseChannel` must be one of alpha, beta, rc, stable; got %q" .Values.releaseChannel) -}}
{{- end -}}

{{- /* metadata database */ -}}
{{- if .Values.postgresql.enabled -}}
{{- if or .Values.externalDatabase.dsn .Values.externalDatabase.existingSecret -}}
{{- fail "molesignal: `postgresql.enabled` is true, but `externalDatabase` is also set. Choose one: set `postgresql.enabled=false` to use the external database, or clear `externalDatabase`." -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" .Values.postgresql.auth.username) -}}
{{- fail "molesignal: `postgresql.auth.username` may contain only letters, digits and underscores" -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" .Values.postgresql.auth.database) -}}
{{- fail "molesignal: `postgresql.auth.database` may contain only letters, digits and underscores" -}}
{{- end -}}
{{- if and .Values.postgresql.auth.password (not (regexMatch "^[A-Za-z0-9._~-]+$" .Values.postgresql.auth.password)) -}}
{{- fail "molesignal: `postgresql.auth.password` may contain only letters, digits and `-._~`, because it is embedded in the connection string. Use a generated password, or an `externalDatabase` DSN with percent-encoding." -}}
{{- end -}}
{{- else if not (or .Values.externalDatabase.dsn .Values.externalDatabase.existingSecret) -}}
{{- fail "molesignal: no metadata database. Enable the bundled one with `postgresql.enabled=true`, or set `externalDatabase.dsn` / `externalDatabase.existingSecret`." -}}
{{- end -}}

{{- /* object store */ -}}
{{- if .Values.rustfs.enabled -}}
{{- if or .Values.objectStore.endpoint .Values.objectStore.accessKey .Values.objectStore.secretKey .Values.objectStore.existingSecret -}}
{{- fail "molesignal: `rustfs.enabled` is true, but `objectStore.endpoint` / credentials are also set. Choose one: set `rustfs.enabled=false` to use your own object store, or clear those values." -}}
{{- end -}}
{{- if not (has .Values.objectStore.region (list "" "us-east-1")) -}}
{{- fail "molesignal: the bundled RustFS only serves region us-east-1; leave `objectStore.region` at its default" -}}
{{- end -}}
{{- else -}}
{{- if or (and .Values.objectStore.accessKey (not .Values.objectStore.secretKey)) (and .Values.objectStore.secretKey (not .Values.objectStore.accessKey)) -}}
{{- fail "molesignal: `objectStore.accessKey` and `objectStore.secretKey` must be set together" -}}
{{- end -}}
{{- if and .Values.objectStore.existingSecret (or .Values.objectStore.accessKey .Values.objectStore.secretKey) -}}
{{- fail "molesignal: set either `objectStore.existingSecret` or the inline `objectStore.accessKey` / `secretKey`, not both" -}}
{{- end -}}
{{- end -}}
{{- if not .Values.objectStore.bucket -}}
{{- fail "molesignal: `objectStore.bucket` is required" -}}
{{- end -}}

{{- /* secrets */ -}}
{{- if .Values.secrets.cipherKey -}}
{{- if ne (len (b64dec .Values.secrets.cipherKey)) 32 -}}
{{- fail "molesignal: `secrets.cipherKey` must be 32 random bytes, base64 encoded (`openssl rand -base64 32`)" -}}
{{- end -}}
{{- end -}}

{{- /* web layer */ -}}
{{- if .Values.web.enabled -}}
{{- if eq .Values.mode "standalone" -}}
{{- fail "molesignal: `web.enabled` needs `mode: distributed`; in standalone mode the UI is served by the application itself" -}}
{{- end -}}
{{- if not .Values.web.image.repository -}}
{{- fail "molesignal: `web.image.repository` is required when `web.enabled`: no web image is published yet, build one from deploy/docker/Dockerfile.web in the application repository" -}}
{{- end -}}
{{- end -}}

{{- /* autoscaling */ -}}
{{- range $role := list "router" "querier" -}}
{{- $cfg := index $.Values $role -}}
{{- if and $cfg.autoscaling.enabled (not (or $cfg.autoscaling.targetCPUUtilizationPercentage $cfg.autoscaling.targetMemoryUtilizationPercentage)) -}}
{{- fail (printf "molesignal: `%s.autoscaling` is enabled but has no CPU or memory target" $role) -}}
{{- end -}}
{{- end -}}
{{- end -}}
