CREATE TABLE IF NOT EXISTS approval_audit_facts (
    approval_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    assignee_roles_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    decision VARCHAR(16) NOT NULL DEFAULT '',
    timed_out BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    terminal_at TIMESTAMPTZ NULL,
    decision_duration_seconds BIGINT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_approval_audit_fact_status CHECK (status IN ('PENDING', 'DECIDED', 'CANCELLED')),
    CONSTRAINT ck_approval_audit_fact_decision CHECK (decision IN ('', 'APPROVE', 'REJECT')),
    CONSTRAINT ck_approval_audit_fact_duration CHECK (
        decision_duration_seconds IS NULL OR decision_duration_seconds >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_approval_audit_facts_tenant_created
    ON approval_audit_facts (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_approval_audit_facts_tenant_terminal
    ON approval_audit_facts (tenant_id, terminal_at DESC)
    WHERE terminal_at IS NOT NULL;

INSERT INTO approval_audit_facts (
    approval_id, tenant_id, run_id, node_id, assignee_roles_json,
    status, decision, timed_out, created_at, terminal_at,
    decision_duration_seconds, updated_at
)
SELECT
    approval_id, tenant_id, run_id, node_id, assignee_roles_json,
    status, decision, timed_out, created_at,
    CASE
        WHEN status = 'DECIDED' THEN decided_at
        WHEN status = 'CANCELLED' THEN updated_at
        ELSE NULL
    END,
    CASE
        WHEN status = 'DECIDED' AND decided_at IS NOT NULL
            THEN GREATEST(0, EXTRACT(EPOCH FROM (decided_at - created_at))::BIGINT)
        WHEN status = 'CANCELLED'
            THEN GREATEST(0, EXTRACT(EPOCH FROM (updated_at - created_at))::BIGINT)
        ELSE NULL
    END,
    updated_at
FROM run_approvals
ON CONFLICT (approval_id) DO NOTHING;
