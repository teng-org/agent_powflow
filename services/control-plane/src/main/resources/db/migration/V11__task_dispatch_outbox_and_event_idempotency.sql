ALTER TABLE task_events
    ADD COLUMN IF NOT EXISTS event_key TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS uk_task_events_tenant_event_key
    ON task_events (tenant_id, event_key)
    WHERE event_key <> '';

CREATE TABLE IF NOT EXISTS task_dispatch_outbox (
    outbox_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by TEXT NOT NULL DEFAULT '',
    locked_until TIMESTAMPTZ NULL,
    last_error TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ NULL,
    dead_lettered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    row_version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uk_task_dispatch_outbox_task UNIQUE (task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_dispatch_outbox_claim
    ON task_dispatch_outbox (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_task_dispatch_outbox_tenant_created
    ON task_dispatch_outbox (tenant_id, created_at DESC);
