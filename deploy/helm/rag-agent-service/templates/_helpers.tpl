{{- define "rag-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-agent.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "rag-agent.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
