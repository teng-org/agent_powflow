CREATE TABLE skills (
    skill_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    latest_version_no INTEGER NOT NULL DEFAULT 0,
    draft_definition_json TEXT NOT NULL DEFAULT '{}',
    created_by VARCHAR(128) NOT NULL,
    updated_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX uk_skills_owner_name ON skills (tenant_id, created_by, lower(name));
CREATE INDEX idx_skills_tenant_updated ON skills (tenant_id, updated_at DESC);

CREATE TABLE skill_versions (
    version_id VARCHAR(64) PRIMARY KEY,
    skill_id VARCHAR(64) NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    version_no INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    published_by VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uk_skill_version UNIQUE (skill_id, version_no)
);

CREATE TABLE mcp_connections (
    connection_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    transport VARCHAR(32) NOT NULL DEFAULT 'STREAMABLE_HTTP',
    endpoint_url VARCHAR(2000) NOT NULL,
    auth_type VARCHAR(32) NOT NULL DEFAULT 'NONE',
    secret_ref VARCHAR(256),
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_by VARCHAR(128) NOT NULL,
    updated_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX uk_mcp_connections_owner_name ON mcp_connections (tenant_id, created_by, lower(name));
CREATE INDEX idx_mcp_connections_tenant_updated ON mcp_connections (tenant_id, updated_at DESC);
