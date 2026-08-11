# Chat Plane

- Protocol: SSE streaming chat.
- Orchestration: node-based pipeline (`normalize -> retrieval -> prompt -> generation -> persist`).
- Extensibility: plugin interfaces in `runtime/plugins.py`.
- Optional KB retrieval: calls execution-plane `/api/v1/retrieval/search`.

## Env

- `CHAT_PLANE_PORT` (default `8092`)
- `EXECUTION_API_BASE_URL` (default `http://execution-api:8090`)
- `EXECUTION_ADMIN_TOKEN` (used for retrieval call)
- `CHAT_API_POOL_DEFAULT_CONFIG` (default `services/chat-plane/config/chat_api_pool.default.json`)
- `CHAT_LLM_BASE_URL`
- `CHAT_LLM_MODEL`
- `CHAT_LLM_API_KEY`
- `RUNTIME_CONFIG_PATH` (default `/app/config/runtime-config.json`)
- `POSTGRES_DSN` (可选，若启用 PostgreSQL 持久化则必填)
- `chat_history_store_backend` in runtime config:
  - `postgres_prefer` (默认，优先 PostgreSQL，失败回退文件)
  - `postgres_only` (只用 PostgreSQL)
  - `file_only` (只用本地 JSON)
- `chat_history_store_path` in runtime config (default `/app/data/chat-plane/chat_history_store.json`)
- `chat_history_disk_flush_interval_ms` in runtime config (default `800`)
- P2 memory retrieval settings in runtime config:
  - `memory_query_rewrite_enabled` / `memory_query_rewrite_use_llm` / `memory_query_rewrite_max_chars`
  - `memory_query_rewrite_min_chars` / `memory_query_rewrite_min_overlap_ratio` / `memory_query_rewrite_max_len_ratio`
  - `memory_session_rag_enabled` / `memory_session_rag_top_k`
  - `memory_session_keyword_top_k`
  - `memory_session_vector_enabled` / `memory_session_vector_top_k`
  - `memory_session_fusion_weight_keyword` / `memory_session_fusion_weight_vector` / `memory_session_fusion_weight_recency`
  - `memory_session_fusion_hybrid_bonus` / `memory_session_fusion_recency_half_life_days`
  - `chat_session_vector_collection` / `chat_session_vector_timeout_seconds`

## PostgreSQL schema migrations

PostgreSQL-backed deployments must provide `POSTGRES_DSN` (or
`CHAT_HISTORY_POSTGRES_DSN`). The container runs the explicit V1/V2 migration
sequence before Uvicorn starts. Business stores validate the migration ledger
and never create or alter tables.

```powershell
$env:POSTGRES_DSN = 'postgresql://apf:apf@localhost:5432/agentpowflow'
python -m runtime.schema_migrations
```

Missing, incomplete, newer, or checksum-drifted schemas fail closed. See
`docs/design/chat-postgresql-schema-migrations-v1.zh-CN.md` for upgrade,
rollback, shared-schema, and integration-test details.

## Stream lifecycle

- Active cancellation transitions `RUNNING -> CANCELLING -> CANCELLED` and interrupts the local provider task without waiting for another token.
- Terminal runs cannot be cancelled or overwritten by late results.
- Stream and fallback provider failure marks the run `FAILED`.
- Client disconnect cancels server-side work and persists no partial assistant message.
- Concurrent streams use request-local event emitters and cannot cross-deliver events.

Provider retry defaults are `chat_request_retry_max=2`,
`chat_stream_retry_max=2`, `chat_provider_retry_initial_ms=250`, and
`chat_provider_retry_max_ms=5000`. Transient HTTP/transport failures retry;
authentication failures do not. Numeric `Retry-After` is honored up to 60s.

API Pool circuit breaking is strict: an endpoint is excluded throughout its OPEN window. After expiry it enters HALF_OPEN and admits exactly one probe; success closes the circuit, failure opens a fresh full window, and cancellation releases the probe claim. Probe claims are process-local and are cleared while restoring persisted state, so a restart cannot strand an endpoint in a false in-flight state. The selector never falls back to an actively open endpoint when all providers are unhealthy.

Commercial-provider verification is intentionally separate from normal tests. `tools/provider_contract.py` requires explicit paid-call approval, an environment-only key, current provider prices and a per-run cost ceiling; see `docs/design/real-provider-contract-gate-v1.zh-CN.md`.

Disconnect currently means cancellation, not stream resumption. See
`docs/design/chat-sse-run-reliability-v1.zh-CN.md`.

## API

- `POST /api/v1/chat/sessions`
- `GET /api/v1/chat/sessions`
- `PATCH /api/v1/chat/sessions/{session_id}` (rename)
- `DELETE /api/v1/chat/sessions/{session_id}`
- `GET /api/v1/chat/sessions/{session_id}/messages`
- `POST /api/v1/chat/sessions/{session_id}/messages/stream`
- `POST /api/v1/chat/runs/{run_id}/cancel`
- `GET /api/v1/chat/runs/{run_id}`
- `GET /api/v1/chat/agent-canvas/schema`
- `GET/PATCH /api/v1/admin/chat/config`
- `GET/POST/DELETE /api/v1/admin/chat/api-pool`
- `POST /api/v1/admin/chat/api-pool/{endpoint_id}/set-default`
- `POST /api/v1/admin/chat/api-pool/reload-default`
- `GET /api/v1/admin/chat/store/status`
- `POST /api/v1/admin/chat/store/flush`
