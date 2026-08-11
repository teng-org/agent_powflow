ALTER TABLE run_node_executions
    DROP CONSTRAINT IF EXISTS ck_run_node_execution_status;

ALTER TABLE run_node_executions
    ADD CONSTRAINT ck_run_node_execution_status
    CHECK (status IN ('RUNNING', 'WAITING_APPROVAL', 'SUCCEEDED', 'FAILED'));

CREATE TABLE IF NOT EXISTS run_approvals (
    approval_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id VARCHAR(128) NOT NULL,
    node_type VARCHAR(64) NOT NULL,
    title VARCHAR(160) NOT NULL,
    instructions VARCHAR(2000) NOT NULL DEFAULT '',
    assignee_roles_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    decision VARCHAR(16) NOT NULL DEFAULT '',
    decision_reason VARCHAR(1000) NOT NULL DEFAULT '',
    decided_by VARCHAR(128) NOT NULL DEFAULT '',
    checkpoint_json TEXT NOT NULL,
    timeout_action VARCHAR(16) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ NULL,
    timed_out BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL,
    CONSTRAINT uk_run_approval_node UNIQUE (tenant_id, run_id, node_id),
    CONSTRAINT ck_run_approval_status CHECK (status IN ('PENDING', 'DECIDED', 'CANCELLED')),
    CONSTRAINT ck_run_approval_decision CHECK (decision IN ('', 'APPROVE', 'REJECT')),
    CONSTRAINT ck_run_approval_timeout_action CHECK (timeout_action IN ('APPROVE', 'REJECT'))
);

CREATE INDEX IF NOT EXISTS idx_run_approvals_tenant_status
    ON run_approvals (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_run_approvals_expiry
    ON run_approvals (status, expires_at ASC);
