ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(128) NOT NULL DEFAULT 'system';

ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS ck_knowledge_bases_visibility;
ALTER TABLE knowledge_bases
    ADD CONSTRAINT ck_knowledge_bases_visibility CHECK (visibility IN ('PRIVATE', 'WORKSPACE', 'SHARED_ALL'));

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) NOT NULL DEFAULT 'PRIVATE';

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner
    ON knowledge_bases (tenant_id, owner_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_workflows_owner
    ON workflows (tenant_id, created_by, updated_at DESC);

CREATE TABLE IF NOT EXISTS agents (
    agent_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    owner_user_id VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    model_config_json TEXT NOT NULL DEFAULT '{}',
    knowledge_base_ids_json TEXT NOT NULL DEFAULT '[]',
    workflow_id VARCHAR(64) NULL,
    visibility VARCHAR(32) NOT NULL DEFAULT 'PRIVATE',
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    latest_version_no INTEGER NOT NULL DEFAULT 0,
    row_version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_agents_visibility CHECK (visibility IN ('PRIVATE', 'WORKSPACE')),
    CONSTRAINT ck_agents_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED'))
);

CREATE INDEX IF NOT EXISTS idx_agents_owner
    ON agents (tenant_id, owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_versions (
    version_id VARCHAR(64) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    version_no INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    published_by VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_agent_version UNIQUE (agent_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_agent_versions_tenant_agent
    ON agent_versions (tenant_id, agent_id, version_no DESC);

CREATE TABLE IF NOT EXISTS deployments (
    deployment_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    owner_user_id VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    slug VARCHAR(96) NOT NULL,
    asset_type VARCHAR(32) NOT NULL,
    asset_id VARCHAR(64) NOT NULL,
    asset_version_no INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_deployments_slug UNIQUE (slug),
    CONSTRAINT ck_deployments_asset_type CHECK (asset_type IN ('AGENT', 'WORKFLOW')),
    CONSTRAINT ck_deployments_status CHECK (status IN ('ACTIVE', 'PAUSED', 'ARCHIVED')),
    CONSTRAINT ck_deployments_rate_limit CHECK (rate_limit_per_minute BETWEEN 1 AND 100000)
);

CREATE INDEX IF NOT EXISTS idx_deployments_owner
    ON deployments (tenant_id, owner_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_deployments_asset
    ON deployments (tenant_id, asset_type, asset_id);

CREATE TABLE IF NOT EXISTS deployment_api_keys (
    api_key_id VARCHAR(64) PRIMARY KEY,
    deployment_id VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    owner_user_id VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    key_prefix VARCHAR(24) NOT NULL,
    secret_hash VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    expires_at TIMESTAMPTZ NULL,
    last_used_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ NULL,
    CONSTRAINT uk_deployment_api_key_prefix UNIQUE (key_prefix),
    CONSTRAINT ck_deployment_api_key_status CHECK (status IN ('ACTIVE', 'REVOKED'))
);

CREATE INDEX IF NOT EXISTS idx_deployment_api_keys_deployment
    ON deployment_api_keys (tenant_id, deployment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS deployment_invocations (
    invocation_id VARCHAR(64) PRIMARY KEY,
    deployment_id VARCHAR(64) NOT NULL,
    api_key_id VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL,
    input_bytes BIGINT NOT NULL DEFAULT 0,
    output_bytes BIGINT NOT NULL DEFAULT 0,
    duration_ms BIGINT NULL,
    error_code VARCHAR(128) NOT NULL DEFAULT '',
    trace_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deployment_invocations_audit
    ON deployment_invocations (tenant_id, deployment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_installation (
    installation_id VARCHAR(32) PRIMARY KEY,
    state VARCHAR(32) NOT NULL DEFAULT 'REQUIRED',
    site_name VARCHAR(128) NOT NULL DEFAULT 'Powflow',
    public_console_url VARCHAR(512) NOT NULL DEFAULT '',
    public_api_url VARCHAR(512) NOT NULL DEFAULT '',
    provider_ready BOOLEAN NOT NULL DEFAULT FALSE,
    storage_ready BOOLEAN NOT NULL DEFAULT FALSE,
    completed_by VARCHAR(128) NULL,
    completed_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_platform_installation_state CHECK (state IN ('REQUIRED', 'IN_PROGRESS', 'READY'))
);

INSERT INTO platform_installation (installation_id)
VALUES ('primary')
ON CONFLICT (installation_id) DO NOTHING;
