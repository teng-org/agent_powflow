CREATE TABLE IF NOT EXISTS run_node_attempts (
    node_attempt_id VARCHAR(64) PRIMARY KEY,
    node_execution_id VARCHAR(64) NOT NULL REFERENCES run_node_executions(node_execution_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id VARCHAR(128) NOT NULL,
    attempt_no INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    duration_ms BIGINT NULL,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    retry_delay_ms BIGINT NOT NULL DEFAULT 0,
    error_type VARCHAR(256) NOT NULL DEFAULT '',
    error_message VARCHAR(2000) NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uk_run_node_attempt UNIQUE (tenant_id, run_id, node_id, attempt_no),
    CONSTRAINT ck_run_node_attempt_no CHECK (attempt_no BETWEEN 1 AND 5),
    CONSTRAINT ck_run_node_attempt_status CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CONSTRAINT ck_run_node_attempt_delay CHECK (retry_delay_ms BETWEEN 0 AND 60000)
);

CREATE INDEX IF NOT EXISTS idx_run_node_attempts_run
    ON run_node_attempts (tenant_id, run_id, node_id, attempt_no ASC);
