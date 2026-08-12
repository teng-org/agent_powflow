#!/usr/bin/env bash
set -Eeuo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; env_file="/etc/agent-powflow/agent-powflow.env"; build=false; pull=false
while [[ $# -gt 0 ]]; do case "$1" in --env-file) env_file="${2:?missing path}"; shift 2;; --build) build=true; shift;; --pull) pull=true; shift;; *) echo "unknown argument: $1" >&2; exit 2;; esac; done
[[ -f "$env_file" ]] || { echo "environment file not found: $env_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$env_file")" =~ ^(600|640)$ ]] || { echo "environment file permissions must be 600 or 640" >&2; exit 1; }
grep -Eq '^CHAT_PROVIDER_MODE=api_strict$' "$env_file" || { echo "CHAT_PROVIDER_MODE must be api_strict" >&2; exit 1; }
grep -Eq '^WORKFLOW_PROVIDER_MODE=api_strict$' "$env_file" || { echo "WORKFLOW_PROVIDER_MODE must be api_strict" >&2; exit 1; }
if grep -Eq '=(change-me|admin123|dev-admin-token|dev-internal-token|minioadmin|guest|)$' "$env_file"; then echo "blank or development credential found" >&2; exit 1; fi
compose=(docker compose -p agentpowflow --profile edge-gateway --env-file "$env_file" -f "$project_dir/infra/compose/docker-compose.stack.yml" -f "$project_dir/infra/compose/docker-compose.production.yml")
"${compose[@]}" config --quiet; $pull && "${compose[@]}" pull --ignore-buildable
args=(up -d --remove-orphans); $build && args+=(--build); "${compose[@]}" "${args[@]}"
"$project_dir/deploy/rocky/check-services.sh" --env-file "$env_file"
printf 'DEPLOY_OK version=%s\n' "$(tr -d '\r\n' < "$project_dir/VERSION")"
