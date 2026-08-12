#!/usr/bin/env bash
set -Eeuo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; env_file="/etc/agent-powflow/agent-powflow.env"
[[ "${1:-}" != "--env-file" ]] || { env_file="${2:?missing path}"; shift 2; }
compose=(docker compose -p agentpowflow --profile edge-gateway --env-file "$env_file" -f "$project_dir/infra/compose/docker-compose.stack.yml" -f "$project_dir/infra/compose/docker-compose.production.yml")
required=(postgres mongodb rabbitmq minio qdrant control-plane execution-api execution-worker chat-plane web-app web-admin kb-admin-web edge-gateway); failed=0
for service in "${required[@]}"; do cid="$("${compose[@]}" ps -q "$service")"; if [[ -z "$cid" || "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" != true ]]; then echo "SERVICE_FAILED name=$service" >&2; failed=1; else echo "SERVICE_OK name=$service"; fi; done
(( failed == 0 )) || exit 1
network="agentpowflow_default"
probe() { docker run --rm --network "$network" curlimages/curl:8.12.1 -fsS --max-time 15 "$2" >/dev/null; echo "HTTP_OK name=$1"; }
probe control-plane http://control-plane:8080/api/v1/system/health
probe execution-api http://execution-api:8090/api/v1/system/health
probe chat-plane http://chat-plane:8092/api/v1/system/health
probe web-app http://web-app:5173/
probe web-admin http://web-admin:5174/
probe kb-admin-web http://kb-admin-web:5180/
echo SERVICE_CHECK_OK
