#!/bin/sh
set -eu
: "${MINIO_ENDPOINT:?MINIO_ENDPOINT is required}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required}"
: "${ALERTMANAGER_WEBHOOK_TOKEN:?ALERTMANAGER_WEBHOOK_TOKEN is required}"
alias_name="agent-powflow-minio"; scheme="${MINIO_SCHEME:-http}"; attempt=0
case "$scheme" in http|https) ;; *) exit 1;; esac
until mc alias set "$alias_name" "${scheme}://${MINIO_ENDPOINT}" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || exit 1; sleep 2
done
output="$(mc admin prometheus generate "$alias_name" 2>/dev/null)"
token="$(printf '%s\n' "$output" | sed -n 's/^[[:space:]]*bearer_token:[[:space:]]*//p' | head -n 1)"
[ -n "$token" ] || { echo "MINIO_PROMETHEUS_TOKEN_INIT_FAILED reason=empty_token" >&2; exit 1; }
umask 077
printf '%s' "$token" > /shared/.minio-bearer-token.tmp; chmod 0444 /shared/.minio-bearer-token.tmp; mv /shared/.minio-bearer-token.tmp /shared/minio-bearer-token
printf '%s' "$ALERTMANAGER_WEBHOOK_TOKEN" > /shared/.alertmanager-webhook-token.tmp; chmod 0444 /shared/.alertmanager-webhook-token.tmp; mv /shared/.alertmanager-webhook-token.tmp /shared/alertmanager-webhook-token
echo "MINIO_PROMETHEUS_TOKEN_INIT_OK"
