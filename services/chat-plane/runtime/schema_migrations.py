from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
import sys
import time
from typing import Callable

import psycopg
from psycopg.rows import dict_row


MIGRATION_LOCK_KEY = 4_701_126_941_038_517_924
MIGRATION_TABLE = "chat_schema_migrations"


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


def postgres_dsn() -> str:
    for candidate in (
        os.getenv("CHAT_HISTORY_POSTGRES_DSN", "").strip(),
        os.getenv("POSTGRES_DSN", "").strip(),
    ):
        if candidate:
            return candidate
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "agentpowflow")
    user = os.getenv("POSTGRES_USER", "apf")
    password = os.getenv("POSTGRES_PASSWORD", "apf")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


V1_CORE_SQL = """
CREATE TABLE IF NOT EXISTS chat_history_store_snapshots (
  snapshot_id SMALLINT PRIMARY KEY,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  title TEXT NOT NULL,
  kb_id TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_session_title_meta (
  session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  is_stable BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS chat_messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_session_memory (
  session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  short_summary TEXT NOT NULL DEFAULT '',
  long_summary TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_user_profiles (
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  profile_summary TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, actor_id)
);
CREATE TABLE IF NOT EXISTS chat_memory_items (
  item_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  importance DOUBLE PRECISION NOT NULL,
  kind TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
""".strip()


V2_MEMORY_RUNTIME_SQL = """
ALTER TABLE chat_user_profiles ADD COLUMN IF NOT EXISTS last_profile_message_at TIMESTAMPTZ NULL;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'conversation';
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS ttl_at TIMESTAMPTZ NULL;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS importance_v2 DOUBLE PRECISION NULL;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS conflict_state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS shadowed_by_item_id TEXT NULL;
CREATE TABLE IF NOT EXISTS chat_session_summaries (
  summary_id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  summary_type TEXT NOT NULL,
  content TEXT NOT NULL,
  source_message_start_id TEXT NULL,
  source_message_end_id TEXT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  is_current BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_user_recent_activities (
  activity_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  session_id TEXT NULL,
  activity_type TEXT NOT NULL,
  activity_text TEXT NOT NULL,
  importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  ttl_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_memory_fusion_logs (
  fusion_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  budget_total_tokens INTEGER NOT NULL,
  budget_used_tokens INTEGER NOT NULL,
  layer_stats JSONB NOT NULL,
  top_items JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_session_segments (
  segment_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  source_message_ids TEXT[] NOT NULL,
  text_content TEXT NOT NULL,
  token_estimate INTEGER NOT NULL DEFAULT 0,
  keywords TEXT[] NULL,
  embedding_ref TEXT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS chat_memory_rerank_jobs (
  job_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  request_limit INTEGER NOT NULL DEFAULT 200,
  status TEXT NOT NULL,
  result_json JSONB NULL,
  error_message TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS chat_api_pool_state (
  snapshot_id SMALLINT PRIMARY KEY,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_actor_updated ON chat_sessions(tenant_id, actor_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_title_meta_updated ON chat_session_title_meta(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_created ON chat_memory_items(session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_importance ON chat_memory_items(session_id, importance DESC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_ttl ON chat_memory_items(session_id, ttl_at);
CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_conflict ON chat_memory_items(session_id, conflict_state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session_current_updated ON chat_session_summaries(session_id, is_current, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_tenant_actor_updated ON chat_session_summaries(tenant_id, actor_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_user_recent_activities_tenant_actor_created ON chat_user_recent_activities(tenant_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_user_recent_activities_ttl ON chat_user_recent_activities(ttl_at);
CREATE INDEX IF NOT EXISTS idx_chat_memory_fusion_logs_tenant_actor_created ON chat_memory_fusion_logs(tenant_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_fusion_logs_session_created ON chat_memory_fusion_logs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_segments_tenant_actor_updated ON chat_session_segments(tenant_id, actor_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_segments_session_updated ON chat_session_segments(session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session_segments_keywords_gin ON chat_session_segments USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_chat_runs_tenant_actor_updated ON chat_runs(tenant_id, actor_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_rerank_jobs_status_created ON chat_memory_rerank_jobs(status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_rerank_jobs_session_updated ON chat_memory_rerank_jobs(session_id, updated_at DESC);
""".strip()


def _apply_sql(dsn: str, statement: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(statement)


def migrations() -> tuple[Migration, ...]:
    return (
        Migration(1, "chat_core_store", hashlib.sha256(V1_CORE_SQL.encode()).hexdigest(), lambda dsn: _apply_sql(dsn, V1_CORE_SQL)),
        Migration(2, "chat_memory_runtime", hashlib.sha256(V2_MEMORY_RUNTIME_SQL.encode()).hexdigest(), lambda dsn: _apply_sql(dsn, V2_MEMORY_RUNTIME_SQL)),
    )


def _validate_declared(declared: tuple[Migration, ...]) -> None:
    versions = [migration.version for migration in declared]
    if versions != list(range(1, len(declared) + 1)):
        raise SchemaMigrationError(f"declared migration versions must be unique, ordered and contiguous from 1: {versions}")
    names = [migration.name for migration in declared]
    if any(not name.strip() for name in names) or len(names) != len(set(names)):
        raise SchemaMigrationError("declared migration names must be non-empty and unique")
    if any(not re.fullmatch(r"[0-9a-f]{64}", migration.checksum) for migration in declared):
        raise SchemaMigrationError("declared migration checksum must be SHA-256")


def _create_metadata_table(cur) -> None:
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      checksum TEXT NOT NULL,
      success BOOLEAN NOT NULL DEFAULT FALSE,
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      applied_at TIMESTAMPTZ NULL,
      execution_ms INTEGER NULL,
      error_message TEXT NOT NULL DEFAULT '')""")


def _load_rows(cur) -> dict[int, dict]:
    cur.execute(f"SELECT version,name,checksum,success,started_at,applied_at,execution_ms,error_message FROM {MIGRATION_TABLE} ORDER BY version")
    return {int(row["version"]): row for row in cur.fetchall()}


def _validate_applied(rows: dict[int, dict], declared: tuple[Migration, ...]) -> None:
    by_version = {migration.version: migration for migration in declared}
    latest = max(by_version, default=0)
    newer = sorted(version for version in rows if version > latest)
    if newer:
        raise SchemaVersionTooNew(f"database schema version {newer[-1]} is newer than supported version {latest}")
    for version, row in rows.items():
        expected = by_version.get(version)
        if expected is None:
            raise SchemaMigrationDrift(f"database contains unknown migration version {version}")
        if bool(row["success"]) and (str(row["name"]) != expected.name or str(row["checksum"]) != expected.checksum):
            raise SchemaMigrationDrift(f"migration V{version} differs from the successfully applied migration")


def migrate_database(dsn: str | None = None) -> list[int]:
    target = (dsn or postgres_dsn()).strip()
    declared = migrations()
    _validate_declared(declared)
    applied_now: list[int] = []
    with psycopg.connect(target, autocommit=True, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            _create_metadata_table(cur)
            rows = _load_rows(cur)
            _validate_applied(rows, declared)
            for migration in declared:
                row = rows.get(migration.version)
                if row and bool(row["success"]):
                    continue
                cur.execute(f"""INSERT INTO {MIGRATION_TABLE}
                  (version,name,checksum,success,started_at,applied_at,execution_ms,error_message)
                  VALUES (%s,%s,%s,FALSE,NOW(),NULL,NULL,'')
                  ON CONFLICT (version) DO UPDATE SET name=EXCLUDED.name,checksum=EXCLUDED.checksum,
                    success=FALSE,started_at=NOW(),applied_at=NULL,execution_ms=NULL,error_message=''""",
                    (migration.version, migration.name, migration.checksum))
                started = time.perf_counter()
                try:
                    migration.apply(target)
                except Exception as exc:
                    elapsed = max(0, int((time.perf_counter() - started) * 1000))
                    cur.execute(f"UPDATE {MIGRATION_TABLE} SET execution_ms=%s,error_message=%s WHERE version=%s", (elapsed, str(exc)[:2000], migration.version))
                    raise SchemaMigrationError(f"migration V{migration.version} {migration.name} failed") from exc
                elapsed = max(0, int((time.perf_counter() - started) * 1000))
                cur.execute(f"UPDATE {MIGRATION_TABLE} SET success=TRUE,applied_at=NOW(),execution_ms=%s,error_message='' WHERE version=%s", (elapsed, migration.version))
                applied_now.append(migration.version)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    return applied_now


def require_database_current(dsn: str | None = None) -> None:
    target = (dsn or postgres_dsn()).strip()
    declared = migrations()
    _validate_declared(declared)
    expected = {migration.version for migration in declared}
    with psycopg.connect(target, autocommit=True, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (MIGRATION_TABLE,))
        if not (cur.fetchone() or {}).get("table_name"):
            raise SchemaMigrationRequired("Chat schema is not migrated; run `python -m runtime.schema_migrations`")
        rows = _load_rows(cur)
    _validate_applied(rows, declared)
    successful = {version for version, row in rows.items() if bool(row["success"])}
    if successful != expected:
        raise SchemaMigrationRequired(f"Chat schema migration is incomplete; missing successful versions: {sorted(expected - successful)}")


def main() -> int:
    try:
        applied = migrate_database()
        current = max((migration.version for migration in migrations()), default=0)
        print(f"CHAT_SCHEMA_MIGRATED versions={','.join(map(str, applied))} current={current}" if applied else f"CHAT_SCHEMA_CURRENT version={current}")
        return 0
    except Exception as exc:
        print(f"CHAT_SCHEMA_MIGRATION_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
