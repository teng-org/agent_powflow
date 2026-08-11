CREATE TABLE IF NOT EXISTS workspace_invitations (
    invitation_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    email VARCHAR(256) NOT NULL,
    role_code VARCHAR(64) NOT NULL DEFAULT 'USER',
    token_prefix VARCHAR(24) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    invited_by VARCHAR(128) NOT NULL,
    accepted_by VARCHAR(128) NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_workspace_invitation_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_workspace_invitation_status CHECK (status IN ('PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED')),
    CONSTRAINT ck_workspace_invitation_role CHECK (role_code IN ('USER', 'TENANT_ADMIN'))
);

CREATE INDEX IF NOT EXISTS idx_workspace_invitations_tenant
    ON workspace_invitations (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_workspace_invitations_email
    ON workspace_invitations (tenant_id, email, status);
