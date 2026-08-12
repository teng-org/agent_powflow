#!/usr/bin/env bash
set -Eeuo pipefail
fail() { printf 'PREFLIGHT_FAILED %s\n' "$*" >&2; exit 1; }
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable"
. /etc/os-release
[[ "${ID:-}" == "rocky" || "${ID_LIKE:-}" == *rhel* ]] || fail "Rocky Linux or RHEL-compatible host required"
[[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "aarch64" ]] || fail "unsupported architecture"
(( $(uname -r | cut -d. -f1) >= 5 )) || fail "Linux kernel 5 or newer required"
(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) >= 4000000 )) || fail "at least 4 GiB available memory required"
free_bytes="$(df -PB1 /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}')"; [[ -n "$free_bytes" ]] || free_bytes="$(df -PB1 / | awk 'NR==2 {print $4}')"
(( free_bytes >= 30000000000 )) || fail "at least 30 GB free disk required"
command -v docker >/dev/null || fail "Docker Engine is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not installed"
compose_version="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
compose_major="${compose_version%%.*}"; compose_tail="${compose_version#*.}"; compose_minor="${compose_tail%%.*}"
(( compose_major > 2 || (compose_major == 2 && compose_minor >= 24) )) || fail "Docker Compose 2.24 or newer is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
printf 'PREFLIGHT_OK os=%s version=%s arch=%s compose=%s\n' "$ID" "${VERSION_ID:-unknown}" "$(uname -m)" "$compose_version"
