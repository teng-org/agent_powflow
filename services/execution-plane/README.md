# Execution Plane

## Control-owned Task callbacks, document commands, and multipart staging

`POST /api/v1/internal/tasks/dispatch` validates a Control-created ingest or document command, including immutable batch identity, and publishes only child work to Celery. `POST /api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/describe` provides an exact bounded non-content preflight for Control batches. Supported Control-owned commands include KB/document summary, chunk/reindex, draft commit, move, tag, and delete. `POST /api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/stage` persists a bounded multipart upload without publishing and now requires an immutable Control Task for every mode: indexed uploads use `DOC_INGEST`, while `UPLOAD_ONLY` and `UPLOAD_AND_CLEAN` use `DOC_UPLOAD`. The matching `DELETE .../staging` endpoint performs exact recovery cleanup. With `TASK_AUTHORITY_CALLBACK_ENABLED=true`, Worker mutations for `state_owner=CONTROL` are sent to Control's internal event API; successful non-index uploads terminate synchronously in Control and do not publish Worker work.

Document move preserves stored MinIO object references. Its SQL ownership mutation, `MOVING` publication barrier, and `MOVE_DOCUMENT_POINTS` repair obligation commit in one PostgreSQL transaction. Qdrant collection mutations are serialized across worker processes with PostgreSQL advisory locks, including concurrent batch moves. A move repair idempotently replaces the target document points, removes the source points, and restores the original publication status only after both collections converge. Explicit document deletion purges every version and delete marker for those exact keys; KB deletion also removes batch, dedup-lock and summary-cache rows. External cleanup failures remain durable in the cross-store repair ledger.

Platform administrators can retry one exact obligation with `POST /api/v1/admin/system/cross-store-repairs/{repair_id}/execute`; batch repair remains available for operational sweeps. A failed Control move can be submitted again even when SQL ownership has already moved, provided the matching durable move repair exists. See `docs/design/document-move-cross-store-repair-v1.zh-CN.md`.

## Public KB write retirement

Public KB/document/Task write endpoints are compatibility-only. They return HTTP `410` with `LEGACY_KB_WRITE_RETIRED` by default; clients must use Control Plane. `EXECUTION_LEGACY_KB_WRITE_ENABLED=true` temporarily re-enables the old implementations for an explicitly managed rollback window. Read endpoints, non-KB system administration, and `/api/v1/internal/kbs/...` service-token routes are unaffected.

Control now proxies document freeze, chunk freeze, and chunk draft save/clear through tenant-scoped internal routes. See `docs/design/control-plane-kb-write-retirement-v1.zh-CN.md`.

## Install

```bash
pip install -r requirements.txt
```

## Run API

```bash
uvicorn api.app.main:app --reload --port 8090
```

## Run Worker

```bash
celery -A workers.app.celery_app:celery_app worker -Q parse_queue,embed_queue,index_queue,agent_queue -l info
```

## Required Services

- PostgreSQL (API pool persistence + retrieval metadata/state)
- Qdrant (vector retrieval)
- RabbitMQ (Celery broker)

## Important Env Vars

- `POSTGRES_DSN` (or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`)
- `QDRANT_URL`
- `QDRANT_API_KEY` (optional)
- `EXECUTION_ADMIN_TOKEN` (admin API auth token)
- `EXECUTION_LEGACY_KB_WRITE_ENABLED` (default `false`; short-lived compatibility switch only)
- `SILICONFLOW_API_KEY` (embedding API token, optional fallback to local embedding)
