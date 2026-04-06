{{/*
Expand the name of the chart.
*/}}
{{- define "draino.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "draino.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart label.
*/}}
{{- define "draino.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "draino.labels" -}}
helm.sh/chart: {{ include "draino.chart" . }}
app.kubernetes.io/name: {{ include "draino.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels.
*/}}
{{- define "draino.selectorLabels" -}}
app.kubernetes.io/name: {{ include "draino.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Service account name.
*/}}
{{- define "draino.serviceAccountName" -}}
{{- if or .Values.serviceAccount.create .Values.nodeAgent.enabled -}}
{{- default (include "draino.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Node agent resource names.
*/}}
{{- define "draino.nodeAgentName" -}}
{{- printf "%s-node-agent" (include "draino.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "draino.nodeAgentSecretName" -}}
{{- printf "%s-node-agent-auth" (include "draino.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
