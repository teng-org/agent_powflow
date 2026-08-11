CREATE TABLE IF NOT EXISTS knowledge_bases (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    name TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'PRIVATE',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    frozen BOOLEAN NOT NULL DEFAULT FALSE,
    summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_dirty BOOLEAN NOT NULL DEFAULT TRUE,
    summary_updated_at TIMESTAMPTZ NULL,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, kb_id),
    CONSTRAINT ck_knowledge_bases_visibility CHECK (visibility IN ('PRIVATE', 'SHARED_ALL'))
);

ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS metadata_owner TEXT NOT NULL DEFAULT 'EXECUTION_LEGACY';

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_tenant_updated
    ON knowledge_bases (tenant_id, updated_at DESC, kb_id);

CREATE TABLE IF NOT EXISTS kb_tenant_bindings (
    owner_tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    target_tenant_id TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_tenant_id, kb_id, target_tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_tenant_bindings_target
    ON kb_tenant_bindings (target_tenant_id, updated_at DESC);
