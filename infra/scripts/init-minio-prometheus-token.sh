#!/bin/sh
set -eu

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT is required}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required}"
: "${ALERTMANAGER_WEBHOOK_TOKEN:?ALERTMANAGER_WEBHOOK_TOKEN is required}"

alias_name="agent-powflow-minio"
scheme="${MINIO_SCHEME:-http}"
case "$scheme" in http|https) ;; *) echo "MINIO_PROMETHEUS_TOKEN_INIT_FAILED reason=invalid_scheme" >&2; exit 1;; esac
attempt=0
until mc alias set "$alias_name" "${scheme}://${MINIO_ENDPOINT}" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "MINIO_PROMETHEUS_TOKEN_INIT_FAILED reason=minio_unavailable" >&2
    exit 1
  fi
  sleep 2
done

output="$(mc admin prometheus generate "$alias_name" 2>/dev/null)"
token=""
while IFS= read -r line; do
  case "$line" in
    *bearer_token:*)
      token="${line#*:}"
      while [ "${token# }" != "$token" ]; do token="${token# }"; done
      break
      ;;
  esac
done <<EOF
$output
EOF
if [ -z "$token" ]; then
  echo "MINIO_PROMETHEUS_TOKEN_INIT_FAILED reason=empty_token" >&2
  exit 1
fi

umask 077
temporary="/shared/.minio-bearer-token.$$"
printf '%s' "$token" > "$temporary"
chmod 0444 "$temporary"
mv "$temporary" /shared/minio-bearer-token
webhook_temporary="/shared/.alertmanager-webhook-token.$$"
printf '%s' "$ALERTMANAGER_WEBHOOK_TOKEN" > "$webhook_temporary"
chmod 0444 "$webhook_temporary"
mv "$webhook_temporary" /shared/alertmanager-webhook-token
echo "MINIO_PROMETHEUS_TOKEN_INIT_OK"
