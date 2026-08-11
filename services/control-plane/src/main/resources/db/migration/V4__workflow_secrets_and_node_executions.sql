CREATE TABLE IF NOT EXISTS workflow_secrets (
    secret_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(500) NOT NULL DEFAULT '',
    ciphertext TEXT NOT NULL,
    fingerprint VARCHAR(16) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(128) NOT NULL,
    updated_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uk_workflow_secrets_tenant_name UNIQUE (tenant_id, name),
    CONSTRAINT ck_workflow_secret_status CHECK (status IN ('ACTIVE', 'DISABLED'))
);

CREATE INDEX IF NOT EXISTS idx_workflow_secrets_tenant
    ON workflow_secrets (tenant_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS run_node_executions (
    node_execution_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id VARCHAR(128) NOT NULL,
    node_type VARCHAR(64) NOT NULL,
    ordinal INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    duration_ms BIGINT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_message VARCHAR(2000) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uk_run_node_execution UNIQUE (tenant_id, run_id, node_id),
    CONSTRAINT ck_run_node_execution_status CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_run_node_executions_run
    ON run_node_executions (tenant_id, run_id, ordinal ASC);
