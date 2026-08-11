CREATE TABLE IF NOT EXISTS provider_invocations (
    invocation_key VARCHAR(64) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    workflow_checksum VARCHAR(64) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    operation VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    owner_token VARCHAR(64) NOT NULL DEFAULT '',
    claim_until TIMESTAMPTZ NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error VARCHAR(2000) NOT NULL DEFAULT '',
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_provider_invocation_status CHECK (status IN ('IN_PROGRESS', 'SUCCEEDED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_provider_invocations_run ON provider_invocations (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_provider_invocations_claim ON provider_invocations (status, claim_until);

CREATE TABLE IF NOT EXISTS run_dead_letters (
    dead_letter_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    failed_task_id VARCHAR(128) NOT NULL,
    source_queue VARCHAR(128) NOT NULL,
    error VARCHAR(2000) NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    replay_count INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR(2000) NOT NULL DEFAULT '',
    replay_requested_at TIMESTAMPTZ NULL,
    replayed_at TIMESTAMPTZ NULL,
    replayed_by VARCHAR(128) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uk_run_dead_letters_task UNIQUE (failed_task_id),
    CONSTRAINT ck_run_dead_letter_status CHECK (status IN ('OPEN', 'REPLAYING', 'REPLAYED', 'DISMISSED'))
);

CREATE INDEX IF NOT EXISTS idx_run_dead_letters_tenant ON run_dead_letters (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_dead_letters_run ON run_dead_letters (run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS run_dead_letter_audit (
    audit_id VARCHAR(64) PRIMARY KEY,
    dead_letter_id VARCHAR(64) NOT NULL REFERENCES run_dead_letters(dead_letter_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor_id VARCHAR(128) NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_run_dead_letter_audit_action CHECK (
        action IN ('INGESTED', 'REPLAY_REQUESTED', 'REPLAY_COMPLETED', 'DISMISSED')
    )
);

CREATE INDEX IF NOT EXISTS idx_run_dead_letter_audit_item
    ON run_dead_letter_audit (dead_letter_id, created_at ASC);
