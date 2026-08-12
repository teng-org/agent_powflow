CREATE TABLE javascript_tools (
    tool_id VARCHAR(64) PRIMARY KEY,
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
CREATE UNIQUE INDEX uk_javascript_tools_owner_name ON javascript_tools (tenant_id, created_by, lower(name));
CREATE INDEX idx_javascript_tools_tenant_updated ON javascript_tools (tenant_id, updated_at DESC);

CREATE TABLE javascript_tool_versions (
    version_id VARCHAR(64) PRIMARY KEY,
    tool_id VARCHAR(64) NOT NULL REFERENCES javascript_tools(tool_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    version_no INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    published_by VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uk_javascript_tool_version UNIQUE (tool_id, version_no)
);
