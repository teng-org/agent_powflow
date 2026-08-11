CREATE TABLE IF NOT EXISTS run_dispatch_outbox (
    outbox_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    locked_by VARCHAR(128) NOT NULL DEFAULT '',
    locked_until TIMESTAMPTZ NULL,
    last_error VARCHAR(2000) NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ NULL,
    dead_lettered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uk_run_dispatch_outbox_run UNIQUE (run_id),
    CONSTRAINT ck_run_dispatch_outbox_status CHECK (
        status IN ('PENDING', 'IN_FLIGHT', 'RETRY', 'PUBLISHED', 'DEAD_LETTER', 'CANCELLED')
    )
);

CREATE INDEX IF NOT EXISTS idx_run_dispatch_outbox_ready
    ON run_dispatch_outbox (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_run_dispatch_outbox_tenant
    ON run_dispatch_outbox (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS run_execution_leases (
    run_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    lease_token VARCHAR(64) NOT NULL,
    worker_id VARCHAR(200) NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt_no INTEGER NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT ck_run_execution_lease_status CHECK (status IN ('ACTIVE', 'RELEASED'))
);

CREATE INDEX IF NOT EXISTS idx_run_execution_leases_expiry
    ON run_execution_leases (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_run_execution_leases_tenant
    ON run_execution_leases (tenant_id, updated_at DESC);
