#!/usr/bin/env bash
set -Eeuo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; env_file="/etc/agent-powflow/agent-powflow.env"; build=false; pull=false
while [[ $# -gt 0 ]]; do case "$1" in --env-file) env_file="${2:?missing path}"; shift 2;; --build) build=true; shift;; --pull) pull=true; shift;; *) echo "unknown argument: $1" >&2; exit 2;; esac; done
[[ -f "$env_file" ]] || { echo "environment file not found: $env_file" >&2; exit 1; }
"$project_dir/deploy/rocky/preflight.sh"
[[ "$(stat -c '%a' "$env_file")" =~ ^(600|640)$ ]] || { echo "environment file permissions must be 600 or 640" >&2; exit 1; }
grep -Eq '^CHAT_PROVIDER_MODE=api_strict$' "$env_file" || { echo "CHAT_PROVIDER_MODE must be api_strict" >&2; exit 1; }
grep -Eq '^WORKFLOW_PROVIDER_MODE=api_strict$' "$env_file" || { echo "WORKFLOW_PROVIDER_MODE must be api_strict" >&2; exit 1; }
required_secrets=(POSTGRES_PASSWORD MONGO_ROOT_PASSWORD RABBITMQ_PASSWORD MINIO_ROOT_ACCESS_KEY MINIO_ROOT_SECRET_KEY MINIO_APP_ACCESS_KEY MINIO_APP_SECRET_KEY MINIO_GOVERNANCE_ACCESS_KEY MINIO_GOVERNANCE_SECRET_KEY ALERTMANAGER_WEBHOOK_TOKEN QDRANT_API_KEY EXECUTION_ADMIN_TOKEN EXECUTION_ADMIN_PASSWORD EXECUTION_JWT_SECRET EXECUTION_JWT_KEY_ID CONTROL_PLANE_JWT_SECRET CONTROL_PLANE_JWT_KEY_ID CONTROL_PLANE_INTERNAL_TOKEN WORKFLOW_SECRET_MASTER_KEY)
for key in "${required_secrets[@]}"; do
  value="$(sed -n "s/^${key}=//p" "$env_file" | tail -n 1)"
  [[ -n "$value" ]] || { echo "required production value is blank: $key" >&2; exit 1; }
  [[ ! "$value" =~ ^(change-me|admin123|dev-admin-token|dev-internal-token|minioadmin|guest)$ ]] || { echo "development credential found: $key" >&2; exit 1; }
done
for key in PUBLIC_CONSOLE_ORIGIN PUBLIC_ADMIN_ORIGIN PUBLIC_KB_ORIGIN PUBLIC_API_ORIGIN; do
  value="$(sed -n "s/^${key}=//p" "$env_file" | tail -n 1)"
  [[ "$value" =~ ^https://[^/]+$ ]] || { echo "$key must be an HTTPS origin without a trailing slash" >&2; exit 1; }
  [[ "$value" != *example.com* ]] || { echo "$key still contains the example.com placeholder" >&2; exit 1; }
done
compose=(docker compose -p agentpowflow --profile edge-gateway --env-file "$env_file" -f "$project_dir/infra/compose/docker-compose.stack.yml" -f "$project_dir/infra/compose/docker-compose.production.yml")
"${compose[@]}" config --quiet; $pull && "${compose[@]}" pull --ignore-buildable
args=(up -d --remove-orphans); $build && args+=(--build); "${compose[@]}" "${args[@]}"
"$project_dir/deploy/rocky/check-services.sh" --env-file "$env_file"
printf 'DEPLOY_OK version=%s\n' "$(tr -d '\r\n' < "$project_dir/VERSION")"
