CREATE TABLE IF NOT EXISTS approval_alerts (
    alert_id VARCHAR(64) PRIMARY KEY,
    dedupe_key VARCHAR(256) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    approval_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    alert_type VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    message VARCHAR(500) NOT NULL,
    assignee_roles_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    acknowledged_at TIMESTAMPTZ NULL,
    acknowledged_by VARCHAR(128) NOT NULL DEFAULT '',
    resolved_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uk_approval_alert_dedupe UNIQUE (dedupe_key),
    CONSTRAINT ck_approval_alert_type CHECK (alert_type IN ('DUE_SOON', 'OVERDUE', 'TIMED_OUT')),
    CONSTRAINT ck_approval_alert_severity CHECK (severity IN ('WARNING', 'CRITICAL')),
    CONSTRAINT ck_approval_alert_status CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED'))
);

CREATE INDEX IF NOT EXISTS idx_approval_alerts_tenant_status
    ON approval_alerts (tenant_id, status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_alerts_approval
    ON approval_alerts (approval_id, opened_at DESC);

CREATE TABLE IF NOT EXISTS approval_alert_deliveries (
    delivery_id VARCHAR(64) PRIMARY KEY,
    alert_id VARCHAR(64) NOT NULL REFERENCES approval_alerts(alert_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    channel VARCHAR(16) NOT NULL,
    destination_host VARCHAR(255) NOT NULL DEFAULT '',
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
    CONSTRAINT uk_approval_alert_delivery_channel UNIQUE (alert_id, channel),
    CONSTRAINT ck_approval_alert_delivery_channel CHECK (channel IN ('WEBHOOK')),
    CONSTRAINT ck_approval_alert_delivery_status CHECK (
        status IN ('DISABLED', 'PENDING', 'IN_FLIGHT', 'RETRY', 'PUBLISHED', 'DEAD_LETTER')
    )
);

CREATE INDEX IF NOT EXISTS idx_approval_alert_delivery_ready
    ON approval_alert_deliveries (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_approval_alert_delivery_tenant
    ON approval_alert_deliveries (tenant_id, created_at DESC);
