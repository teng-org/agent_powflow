from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import os
import re
import sys
import time
from typing import Callable

import psycopg
from psycopg.rows import dict_row


MIGRATION_LOCK_KEY = 4_701_126_941_038_517_923
MIGRATION_TABLE = "execution_schema_migrations"


class SchemaMigrationError(RuntimeError):
    pass


class SchemaMigrationRequired(SchemaMigrationError):
    pass


class SchemaMigrationDrift(SchemaMigrationError):
    pass


class SchemaVersionTooNew(SchemaMigrationError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    apply: Callable[[str], None]


def _postgres_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if dsn:
        return dsn
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "agentpowflow")
    user = os.getenv("POSTGRES_USER", "apf")
    password = os.getenv("POSTGRES_PASSWORD", "apf")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _apply_v1(dsn: str) -> None:
    # The import is deliberately lazy: the Store validates migration state at
    # runtime, while the migration runner is the only owner of schema writes.
    from retrieval.store import KnowledgeBaseStore

    migration_conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row)
    store = KnowledgeBaseStore(dsn=dsn, qdrant_client=object())
    store._conn = lambda: migration_conn
    store._apply_schema_v1()


def _v1_checksum() -> str:
    from retrieval.store import KnowledgeBaseStore

    source = inspect.getsource(KnowledgeBaseStore._apply_schema_v1).encode("utf-8")
    return hashlib.sha256(source).hexdigest()


V2_API_POOL_SQL = """
CREATE TABLE IF NOT EXISTS api_pool_settings (
    settings_key TEXT PRIMARY KEY,
    default_endpoint_id TEXT NULL,
    default_endpoint_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS api_pool_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS aux_api_pool_settings (
    settings_key TEXT PRIMARY KEY,
    default_endpoint_id TEXT NULL,
    default_endpoint_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS aux_api_pool_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
""".strip()


def _apply_v2(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V2_API_POOL_SQL)


V3_CROSS_STORE_REPAIR_SQL = """
CREATE TABLE IF NOT EXISTS cross_store_repairs (
    repair_id BIGSERIAL PRIMARY KEY,
    store_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    CHECK (status IN ('PENDING','IN_PROGRESS','SUCCEEDED','FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_cross_store_repairs_pending
    ON cross_store_repairs (status, updated_at, repair_id);
CREATE INDEX IF NOT EXISTS idx_cross_store_repairs_resource
    ON cross_store_repairs (tenant_id, kb_id, doc_id, created_at DESC);
""".strip()


def _apply_v3(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V3_CROSS_STORE_REPAIR_SQL)


V4_DOCUMENT_OBJECT_STORAGE_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_object_bucket TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_object_key TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_object_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_object_size_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_content_type TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_object_key TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_text_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE cross_store_repairs ADD COLUMN IF NOT EXISTS payload_json JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_documents_source_object
    ON documents (source_object_bucket, source_object_key)
    WHERE source_object_key <> '';
""".strip()


def _apply_v4(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V4_DOCUMENT_OBJECT_STORAGE_SQL)


V5_STORAGE_CAPACITY_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_object_size_bytes BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS storage_quota_reservations (
    reservation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    reserved_bytes BIGINT NOT NULL CHECK (reserved_bytes > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_storage_quota_reservations_tenant_expiry
    ON storage_quota_reservations (tenant_id, expires_at);
""".strip()


def _apply_v5(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V5_STORAGE_CAPACITY_SQL)


V6_STORAGE_CAPACITY_BACKFILL_SQL = """
UPDATE documents
SET extracted_object_size_bytes = octet_length(raw_text)
WHERE extracted_object_key <> ''
  AND extracted_object_size_bytes = 0
  AND raw_text <> '';
""".strip()


def _apply_v6(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V6_STORAGE_CAPACITY_BACKFILL_SQL)


V7_ALERTMANAGER_ALERTS_SQL = """
CREATE TABLE IF NOT EXISTS alertmanager_alerts (
    fingerprint TEXT PRIMARY KEY,
    alert_name TEXT NOT NULL,
    component TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'warning',
    status TEXT NOT NULL CHECK (status IN ('FIRING','RESOLVED')),
    labels_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    annotations_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    starts_at TIMESTAMPTZ NULL,
    ends_at TIMESTAMPTZ NULL,
    first_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_alertmanager_alerts_status_severity_received
    ON alertmanager_alerts (status, severity, last_received_at DESC);
""".strip()


def _apply_v7(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V7_ALERTMANAGER_ALERTS_SQL)


V8_TASK_STATE_OWNER_SQL = """
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS state_owner TEXT NOT NULL DEFAULT 'EXECUTION';
""".strip()


def _apply_v8(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V8_TASK_STATE_OWNER_SQL)


V9_TASK_BATCH_CANCELLED_ITEMS_SQL = """
ALTER TABLE task_batches
    ADD COLUMN IF NOT EXISTS cancelled_items INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_task_batches_parent_task
    ON task_batches (tenant_id, parent_task_id);
""".strip()


def _apply_v9(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V9_TASK_BATCH_CANCELLED_ITEMS_SQL)


V10_RAG2_INDEX_SQL = """
ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS active_index_revision TEXT NOT NULL DEFAULT 'legacy-v1';
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS lexical_terms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS lexical_tf JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS lexical_token_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS index_revision TEXT NOT NULL DEFAULT 'legacy-v1';
CREATE INDEX IF NOT EXISTS idx_chunks_lexical_terms
    ON chunks USING GIN (lexical_terms);
CREATE INDEX IF NOT EXISTS idx_chunks_index_revision
    ON chunks (tenant_id, kb_id, index_revision, doc_id, chunk_index);
CREATE TABLE IF NOT EXISTS rag_index_manifests (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    index_revision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'BUILDING'
        CHECK (status IN ('BUILDING','ACTIVE','RETIRED','FAILED')),
    parser_id TEXT NOT NULL DEFAULT 'plain-text-v1',
    chunker_id TEXT NOT NULL DEFAULT 'character-v1',
    dense_model_id TEXT NOT NULL DEFAULT '',
    sparse_model_id TEXT NOT NULL DEFAULT 'lexical-ngram-v1',
    reranker_id TEXT NOT NULL DEFAULT '',
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    document_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    lexical_chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, kb_id, index_revision)
);
CREATE INDEX IF NOT EXISTS idx_rag_index_manifests_status
    ON rag_index_manifests (tenant_id, kb_id, status, updated_at DESC);
""".strip()


def _apply_v10(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V10_RAG2_INDEX_SQL)


V11_RAG2_PARENT_CHILD_SQL = """
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS parent_id TEXT NOT NULL DEFAULT '';
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS block_type TEXT NOT NULL DEFAULT 'paragraph';
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS page_start INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS page_end INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_chunks_parent
    ON chunks (tenant_id, kb_id, doc_id, parent_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_section_path
    ON chunks USING GIN (section_path);
CREATE TABLE IF NOT EXISTS chunk_parents (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    parent_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    page_start INTEGER NOT NULL DEFAULT 0,
    page_end INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    index_revision TEXT NOT NULL DEFAULT 'legacy-v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, kb_id, doc_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_chunk_parents_order
    ON chunk_parents (tenant_id, kb_id, doc_id, parent_index);
""".strip()


def _apply_v11(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V11_RAG2_PARENT_CHILD_SQL)


V12_RAG2_SHADOW_INDEX_SQL = """
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS collection_name TEXT NOT NULL DEFAULT '';
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS source_revision TEXT NOT NULL DEFAULT '';
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS build_id TEXT NOT NULL DEFAULT '';
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS gate_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS quality_report JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS build_error TEXT NOT NULL DEFAULT '';
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS built_at TIMESTAMPTZ NULL;
ALTER TABLE rag_index_manifests
    ADD COLUMN IF NOT EXISTS activated_by TEXT NOT NULL DEFAULT '';
ALTER TABLE rag_index_manifests
    DROP CONSTRAINT IF EXISTS rag_index_manifests_gate_status_check;
ALTER TABLE rag_index_manifests
    ADD CONSTRAINT rag_index_manifests_gate_status_check
    CHECK (gate_status IN ('PENDING','PASSED','FAILED','WAIVED'));

CREATE TABLE IF NOT EXISTS rag_index_chunks (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    index_revision TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    lexical_terms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    lexical_tf JSONB NOT NULL DEFAULT '{}'::jsonb,
    lexical_token_count INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT NOT NULL DEFAULT '',
    section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    block_type TEXT NOT NULL DEFAULT 'paragraph',
    page_start INTEGER NOT NULL DEFAULT 0,
    page_end INTEGER NOT NULL DEFAULT 0,
    frozen BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id,kb_id,index_revision,doc_id,chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_rag_index_chunks_doc
    ON rag_index_chunks (tenant_id,kb_id,index_revision,doc_id,chunk_index);
CREATE INDEX IF NOT EXISTS idx_rag_index_chunks_lexical
    ON rag_index_chunks USING GIN (lexical_terms);
CREATE INDEX IF NOT EXISTS idx_rag_index_chunks_parent
    ON rag_index_chunks (tenant_id,kb_id,index_revision,doc_id,parent_id);

CREATE TABLE IF NOT EXISTS rag_index_parents (
    tenant_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    index_revision TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    parent_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    page_start INTEGER NOT NULL DEFAULT 0,
    page_end INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id,kb_id,index_revision,doc_id,parent_id)
);
CREATE INDEX IF NOT EXISTS idx_rag_index_parents_order
    ON rag_index_parents (tenant_id,kb_id,index_revision,doc_id,parent_index);

UPDATE rag_index_manifests m
SET status=CASE
    WHEN m.index_revision=k.active_index_revision THEN 'ACTIVE'
    WHEN m.status='ACTIVE' THEN 'RETIRED'
    ELSE m.status END,
    gate_status=CASE
    WHEN m.index_revision=k.active_index_revision THEN 'PASSED'
    ELSE m.gate_status END
FROM knowledge_bases k
WHERE m.tenant_id=k.tenant_id AND m.kb_id=k.kb_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_index_manifests_one_active
    ON rag_index_manifests (tenant_id,kb_id) WHERE status='ACTIVE';
""".strip()


def _apply_v12(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(V12_RAG2_SHADOW_INDEX_SQL)


def migrations() -> tuple[Migration, ...]:
    return (
        Migration(
            version=1,
            name="execution_store_baseline",
            checksum=_v1_checksum(),
            apply=_apply_v1,
        ),
        Migration(
            version=2,
            name="api_pool_tables",
            checksum=hashlib.sha256(V2_API_POOL_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v2,
        ),
        Migration(
            version=3,
            name="cross_store_repair_ledger",
            checksum=hashlib.sha256(V3_CROSS_STORE_REPAIR_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v3,
        ),
        Migration(
            version=4,
            name="document_object_storage_metadata",
            checksum=hashlib.sha256(V4_DOCUMENT_OBJECT_STORAGE_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v4,
        ),
        Migration(
            version=5,
            name="object_storage_capacity_reservations",
            checksum=hashlib.sha256(V5_STORAGE_CAPACITY_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v5,
        ),
        Migration(
            version=6,
            name="object_storage_capacity_backfill",
            checksum=hashlib.sha256(V6_STORAGE_CAPACITY_BACKFILL_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v6,
        ),
        Migration(
            version=7,
            name="alertmanager_alert_facts",
            checksum=hashlib.sha256(V7_ALERTMANAGER_ALERTS_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v7,
        ),
        Migration(
            version=8,
            name="task_state_authority_owner",
            checksum=hashlib.sha256(V8_TASK_STATE_OWNER_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v8,
        ),
        Migration(
            version=9,
            name="task_batch_cancelled_items",
            checksum=hashlib.sha256(V9_TASK_BATCH_CANCELLED_ITEMS_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v9,
        ),
        Migration(
            version=10,
            name="rag2_index_manifest_and_lexical_features",
            checksum=hashlib.sha256(V10_RAG2_INDEX_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v10,
        ),
        Migration(
            version=11,
            name="rag2_parent_child_content_structure",
            checksum=hashlib.sha256(V11_RAG2_PARENT_CHILD_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v11,
        ),
        Migration(
            version=12,
            name="rag2_shadow_index_snapshots_and_quality_gate",
            checksum=hashlib.sha256(V12_RAG2_SHADOW_INDEX_SQL.encode("utf-8")).hexdigest(),
            apply=_apply_v12,
        ),
    )


def _validate_declared(declared: tuple[Migration, ...]) -> None:
    versions = [migration.version for migration in declared]
    if versions != list(range(1, len(declared) + 1)):
        raise SchemaMigrationError(
            f"declared migration versions must be unique, ordered and contiguous from 1: {versions}"
        )
    names = [migration.name for migration in declared]
    if any(not name.strip() for name in names) or len(names) != len(set(names)):
        raise SchemaMigrationError("declared migration names must be non-empty and unique")
    for migration in declared:
        if not re.fullmatch(r"[0-9a-f]{64}", migration.checksum):
            raise SchemaMigrationError(
                f"migration V{migration.version} has an invalid SHA-256 checksum"
            )


def _create_metadata_table(cur) -> None:
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        checksum TEXT NOT NULL,
        success BOOLEAN NOT NULL DEFAULT FALSE,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        applied_at TIMESTAMPTZ NULL,
        execution_ms INTEGER NULL,
        error_message TEXT NOT NULL DEFAULT '')"""
    )


def _load_rows(cur) -> dict[int, dict]:
    cur.execute(
        f"SELECT version,name,checksum,success,started_at,applied_at,execution_ms,error_message "
        f"FROM {MIGRATION_TABLE} ORDER BY version"
    )
    return {int(row["version"]): row for row in cur.fetchall()}


def _validate_applied(rows: dict[int, dict], declared: tuple[Migration, ...]) -> None:
    declared_by_version = {migration.version: migration for migration in declared}
    latest = max(declared_by_version, default=0)
    newer = sorted(version for version in rows if version > latest)
    if newer:
        raise SchemaVersionTooNew(
            f"database schema version {newer[-1]} is newer than supported version {latest}"
        )
    for version, row in rows.items():
        expected = declared_by_version.get(version)
        if expected is None:
            raise SchemaMigrationDrift(f"database contains unknown migration version {version}")
        if bool(row["success"]) and (
            str(row["name"]) != expected.name or str(row["checksum"]) != expected.checksum
        ):
            raise SchemaMigrationDrift(
                f"migration V{version} differs from the successfully applied migration"
            )


def migrate_database(dsn: str | None = None) -> list[int]:
    target_dsn = (dsn or _postgres_dsn()).strip()
    declared = migrations()
    _validate_declared(declared)
    applied_now: list[int] = []
    with psycopg.connect(target_dsn, autocommit=True, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            _create_metadata_table(cur)
            rows = _load_rows(cur)
            _validate_applied(rows, declared)
            for migration in declared:
                row = rows.get(migration.version)
                if row and bool(row["success"]):
                    continue
                cur.execute(
                    f"""INSERT INTO {MIGRATION_TABLE}
                    (version,name,checksum,success,started_at,applied_at,execution_ms,error_message)
                    VALUES (%s,%s,%s,FALSE,NOW(),NULL,NULL,'')
                    ON CONFLICT (version) DO UPDATE SET
                      name=EXCLUDED.name,checksum=EXCLUDED.checksum,success=FALSE,
                      started_at=NOW(),applied_at=NULL,execution_ms=NULL,error_message=''""",
                    (migration.version, migration.name, migration.checksum),
                )
                started = time.perf_counter()
                try:
                    migration.apply(target_dsn)
                except Exception as exc:
                    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
                    cur.execute(
                        f"UPDATE {MIGRATION_TABLE} SET execution_ms=%s,error_message=%s WHERE version=%s",
                        (elapsed_ms, str(exc)[:2000], migration.version),
                    )
                    raise SchemaMigrationError(
                        f"migration V{migration.version} {migration.name} failed"
                    ) from exc
                elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
                cur.execute(
                    f"""UPDATE {MIGRATION_TABLE}
                    SET success=TRUE,applied_at=NOW(),execution_ms=%s,error_message=''
                    WHERE version=%s""",
                    (elapsed_ms, migration.version),
                )
                applied_now.append(migration.version)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    return applied_now


def require_database_current(dsn: str | None = None) -> None:
    target_dsn = (dsn or _postgres_dsn()).strip()
    declared = migrations()
    _validate_declared(declared)
    expected_versions = {migration.version for migration in declared}
    try:
        with psycopg.connect(target_dsn, autocommit=True, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS table_name", (MIGRATION_TABLE,))
            if not (cur.fetchone() or {}).get("table_name"):
                raise SchemaMigrationRequired(
                    "Execution schema is not migrated; run `python -m retrieval.schema_migrations`"
                )
            rows = _load_rows(cur)
    except SchemaMigrationError:
        raise
    _validate_applied(rows, declared)
    successful = {version for version, row in rows.items() if bool(row["success"])}
    if successful != expected_versions:
        missing = sorted(expected_versions - successful)
        raise SchemaMigrationRequired(
            f"Execution schema migration is incomplete; missing successful versions: {missing}"
        )


def main() -> int:
    try:
        applied = migrate_database()
        current = max((migration.version for migration in migrations()), default=0)
        if applied:
            print(f"EXECUTION_SCHEMA_MIGRATED versions={','.join(str(item) for item in applied)} current={current}")
        else:
            print(f"EXECUTION_SCHEMA_CURRENT version={current}")
        return 0
    except Exception as exc:
        print(f"EXECUTION_SCHEMA_MIGRATION_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
