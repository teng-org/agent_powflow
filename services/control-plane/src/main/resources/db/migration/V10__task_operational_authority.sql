CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL DEFAULT '',
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    batch_id TEXT NOT NULL DEFAULT '',
    batch_name TEXT NOT NULL DEFAULT '',
    batch_is_parent BOOLEAN NOT NULL DEFAULT FALSE,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS state_owner TEXT NOT NULL DEFAULT 'EXECUTION_LEGACY';

CREATE INDEX IF NOT EXISTS idx_tasks_tenant_kb_updated
    ON tasks (tenant_id, kb_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_updated
    ON tasks (tenant_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_batch
    ON tasks (tenant_id, kb_id, batch_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS task_dedup_locks (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    lock_scope TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, kb_id, doc_id, lock_scope)
);

CREATE INDEX IF NOT EXISTS idx_task_dedup_locks_exp ON task_dedup_locks (expires_at);
CREATE INDEX IF NOT EXISTS idx_task_dedup_locks_task ON task_dedup_locks (task_id);

CREATE TABLE IF NOT EXISTS task_events (
    event_id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_ts
    ON task_events (task_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_task_events_tenant_task
    ON task_events (tenant_id, task_id, event_id DESC);

CREATE TABLE IF NOT EXISTS task_batches (
    batch_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    batch_name TEXT NOT NULL DEFAULT '',
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    parent_task_id TEXT NOT NULL DEFAULT '',
    total_items INTEGER NOT NULL DEFAULT 0,
    succeeded_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    running_items INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_batches_tenant_kb_updated
    ON task_batches (tenant_id, kb_id, updated_at DESC);
