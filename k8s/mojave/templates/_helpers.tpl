{{- define "mojave.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mojave.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mojave.labels" -}}
app.kubernetes.io/name: {{ include "mojave.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "mojave.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mojave.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
