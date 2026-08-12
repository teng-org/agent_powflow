from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import time
import uuid

import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import ApiException

from retrieval.rag2 import bm25_score, lexical_term_frequency


class TaskStoreError(RuntimeError):
    pass


class TaskNotFound(TaskStoreError):
    pass


class TaskIdentityConflict(TaskStoreError):
    pass


class InvalidTaskStateTransition(TaskStoreError):
    pass


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


@dataclass
class ChunkRecord:
    tenant_id: str
    kb_id: str
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str
    embedding: list[float]
    metadata: dict


class KnowledgeBaseStore:
    def __init__(self, dsn: str | None = None, qdrant_client=None, object_store_client=None) -> None:
        self.dsn = (dsn or _postgres_dsn()).strip()
        self.collection_prefix = os.getenv("QDRANT_COLLECTION_PREFIX", "apf")
        self.qdrant = qdrant_client or QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=(os.getenv("QDRANT_API_KEY", "") or None),
            timeout=max(5.0, min(float(os.getenv("QDRANT_TIMEOUT_SECONDS", "120")), 600.0)),
        )
        self.object_storage = object_store_client
        self.rag_index_revision = (os.getenv("RAG_INDEX_REVISION", "rag2-v1") or "rag2-v1").strip()
        self._initialized = False

    def _object_store(self):
        if self.object_storage is None:
            from runtime.object_storage import object_storage

            self.object_storage = object_storage
        return self.object_storage

    def _conn(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _collection_name(self, tenant_id: str, kb_id: str) -> str:
        raw = f"{self.collection_prefix}_{tenant_id}_{kb_id}".lower()
        return re.sub(r"[^a-z0-9_]", "_", raw)

    def _index_collection_name(self, tenant_id: str, kb_id: str, index_revision: str) -> str:
        revision = self._strip_nul(index_revision).strip().lower()
        raw = f"{self.collection_prefix}_{tenant_id}_{kb_id}_idx_{revision}"
        normalized = re.sub(r"[^a-z0-9_]", "_", raw)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]  # nosec B324
        return f"{normalized[:180]}_{digest}"

    def get_active_index_revision(self, tenant_id: str, kb_id: str) -> str:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT active_index_revision FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            row = cur.fetchone() or {}
        return str(row.get("active_index_revision") or "legacy-v1")

    def has_index_snapshot(
        self,
        tenant_id: str,
        kb_id: str,
        index_revision: str,
        *,
        allow_pending: bool = False,
    ) -> bool:
        self._ensure_indexes()
        gate_filter = "" if allow_pending else " AND gate_status IN ('PASSED','WAIVED')"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT 1 FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s
                  AND status IN ('BUILDING','ACTIVE','RETIRED')
                  {gate_filter}
                  AND collection_name=%s LIMIT 1""",
                (
                    tenant_id,
                    kb_id,
                    index_revision,
                    self._index_collection_name(tenant_id, kb_id, index_revision),
                ),
            )
            manifest = cur.fetchone() is not None
            if not manifest:
                return False
            cur.execute(
                """SELECT 1 FROM rag_index_chunks
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s LIMIT 1""",
                (tenant_id, kb_id, index_revision),
            )
            return cur.fetchone() is not None

    @contextmanager
    def _qdrant_collection_locks(self, collection_names: list[str]):
        """Serialize collection mutation across API and worker processes."""
        scopes = [f"qdrant-collection:{name}" for name in sorted(set(collection_names)) if name]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                for scope in scopes:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (scope,))
                yield
            finally:
                conn.rollback()

    def _strip_nul(self, value: str | None) -> str:
        return re.sub(r"[\x00\ud800-\udfff]", "", value or "")

    def _apply_schema_v1(self) -> None:
        """Apply the immutable v1 schema migration.

        Runtime code must never call this method directly. It is invoked only
        by ``retrieval.schema_migrations`` while holding the migration lock.
        Future schema changes belong in a new migration version.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_bases (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, name TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'PRIVATE',
                summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ACTIVE',
                frozen BOOLEAN NOT NULL DEFAULT FALSE,
                summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                summary_dirty BOOLEAN NOT NULL DEFAULT TRUE,
                summary_updated_at TIMESTAMPTZ NULL,
                stats JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id))"""
            )
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'PRIVATE'")
            cur.execute(
                """
                UPDATE knowledge_bases
                SET visibility='SHARED_ALL'
                WHERE tenant_id='platform'
                  AND (visibility IS NULL OR visibility='PRIVATE' OR visibility='')
                """
            )
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS frozen BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_dirty BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ NULL")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS kb_tenant_bindings (
                owner_tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL,
                target_tenant_id TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (owner_tenant_id, kb_id, target_tenant_id))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_kb_tenant_bindings_target ON kb_tenant_bindings (target_tenant_id, updated_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                filename TEXT NOT NULL, raw_text TEXT NOT NULL DEFAULT '',
                raw_text_preview TEXT NOT NULL DEFAULT '',
                tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                summary_overview TEXT NOT NULL DEFAULT '',
                summary_text TEXT NOT NULL DEFAULT '',
                summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                summary_dirty BOOLEAN NOT NULL DEFAULT TRUE,
                frozen BOOLEAN NOT NULL DEFAULT FALSE,
                text_size_bytes INTEGER NOT NULL DEFAULT 0, word_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'UPLOADED', chunk_count INTEGER NOT NULL DEFAULT 0,
                embedded_count INTEGER NOT NULL DEFAULT 0, embedding_dirty BOOLEAN NOT NULL DEFAULT TRUE,
                chunk_method TEXT NOT NULL DEFAULT 'sentence',
                chunk_size INTEGER NOT NULL DEFAULT 500,
                chunk_overlap INTEGER NOT NULL DEFAULT 50,
                chunk_delimiter TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id))"""
            )
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS frozen BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags_json JSONB NOT NULL DEFAULT '[]'::jsonb")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_overview TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_text TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_dirty BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_method TEXT NOT NULL DEFAULT 'sentence'")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_size INTEGER NOT NULL DEFAULT 500")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_overlap INTEGER NOT NULL DEFAULT 50")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_delimiter TEXT NOT NULL DEFAULT ''")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, text TEXT NOT NULL,
                frozen BOOLEAN NOT NULL DEFAULT FALSE,
                embedding_json JSONB NOT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id, chunk_id))"""
            )
            cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS frozen BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (tenant_id, kb_id, doc_id, chunk_index)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                task_type TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                filename TEXT NOT NULL DEFAULT '',
                batch_id TEXT NOT NULL DEFAULT '',
                batch_name TEXT NOT NULL DEFAULT '',
                batch_is_parent BOOLEAN NOT NULL DEFAULT FALSE,
                progress_current INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0, progress_pct INTEGER NOT NULL DEFAULT 0,
                cancel_requested BOOLEAN NOT NULL DEFAULT FALSE, error_message TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS filename TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS batch_id TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS batch_name TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS batch_is_parent BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_current INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_total INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_pct INTEGER NOT NULL DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tenant_kb_updated ON tasks (tenant_id, kb_id, updated_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks (tenant_id, kb_id, batch_id, updated_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS task_dedup_locks (
                tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                lock_scope TEXT NOT NULL,
                task_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (tenant_id, kb_id, doc_id, lock_scope))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_dedup_locks_exp ON task_dedup_locks (expires_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_dedup_locks_task ON task_dedup_locks (task_id)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS task_events (
                event_id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, event_type TEXT NOT NULL, stage TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb, ts TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_ts ON task_events (task_id, ts DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS task_batches (
                batch_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL,
                operation TEXT NOT NULL, batch_name TEXT NOT NULL DEFAULT '',
                params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                parent_task_id TEXT NOT NULL DEFAULT '',
                total_items INTEGER NOT NULL DEFAULT 0, succeeded_items INTEGER NOT NULL DEFAULT 0,
                failed_items INTEGER NOT NULL DEFAULT 0, running_items INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_batches_tenant_kb_updated ON task_batches (tenant_id, kb_id, updated_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS chunk_drafts (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
                action TEXT NOT NULL, text TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id, chunk_id))"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS system_settings (
                settings_key TEXT PRIMARY KEY, value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS system_log_entries (
                log_uid TEXT PRIMARY KEY,
                event_id TEXT NOT NULL DEFAULT '',
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                level TEXT NOT NULL DEFAULT 'info',
                category TEXT NOT NULL DEFAULT 'system',
                message TEXT NOT NULL DEFAULT '-',
                source TEXT NOT NULL DEFAULT '-',
                role TEXT NOT NULL DEFAULT '-',
                status TEXT NOT NULL DEFAULT '-',
                path TEXT NOT NULL DEFAULT '-',
                event TEXT NOT NULL DEFAULT '',
                resource_type TEXT NOT NULL DEFAULT '',
                http_status TEXT NOT NULL DEFAULT '',
                auth_result TEXT NOT NULL DEFAULT '',
                trace_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'LOW',
                risk_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                decision TEXT NOT NULL DEFAULT 'ALLOW',
                reason_code TEXT NOT NULL DEFAULT 'GENERIC_EVENT',
                trigger_rule TEXT NOT NULL DEFAULT 'default',
                log_type TEXT NOT NULL DEFAULT 'access',
                file_name TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_system_log_entries_ts ON system_log_entries (ts DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_system_log_entries_risk ON system_log_entries (risk_level, ts DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_system_log_entries_level ON system_log_entries (level, ts DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_system_log_entries_source ON system_log_entries (source, ts DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_system_log_entries_category ON system_log_entries (category, ts DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_schema (
                config_key TEXT PRIMARY KEY,
                data_type TEXT NOT NULL DEFAULT 'json',
                scope_level TEXT NOT NULL DEFAULT 'platform',
                is_secret BOOLEAN NOT NULL DEFAULT FALSE,
                category TEXT NOT NULL DEFAULT 'general',
                description TEXT NOT NULL DEFAULT '',
                default_json JSONB NOT NULL DEFAULT 'null'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_values (
                config_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                value_json JSONB NOT NULL DEFAULT 'null'::jsonb,
                version BIGINT NOT NULL DEFAULT 1,
                updated_by TEXT NOT NULL DEFAULT 'system',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (config_key, scope_type, scope_id))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_config_values_scope ON config_values (scope_type, scope_id, updated_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_releases (
                release_id BIGSERIAL PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PUBLISHED',
                apply_status TEXT NOT NULL DEFAULT 'PENDING',
                applied_at TIMESTAMPTZ NULL,
                apply_error TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                changes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("ALTER TABLE config_releases ADD COLUMN IF NOT EXISTS apply_status TEXT NOT NULL DEFAULT 'PENDING'")
            cur.execute("ALTER TABLE config_releases ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE config_releases ADD COLUMN IF NOT EXISTS apply_error TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE config_releases ADD COLUMN IF NOT EXISTS snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_config_releases_scope ON config_releases (scope_type, scope_id, created_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_audit_logs (
                audit_id BIGSERIAL PRIMARY KEY,
                action TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                release_id BIGINT NOT NULL DEFAULT 0,
                config_key TEXT NOT NULL,
                old_json JSONB NOT NULL DEFAULT 'null'::jsonb,
                new_json JSONB NOT NULL DEFAULT 'null'::jsonb,
                actor_id TEXT NOT NULL DEFAULT 'system',
                trace_id TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("ALTER TABLE config_audit_logs ADD COLUMN IF NOT EXISTS release_id BIGINT NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE config_audit_logs ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_config_audit_scope ON config_audit_logs (scope_type, scope_id, created_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_publish_locks (
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                lock_token TEXT NOT NULL,
                owner_id TEXT NOT NULL DEFAULT '',
                lock_until TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scope_type, scope_id))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_config_publish_locks_until ON config_publish_locks (lock_until)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS config_publish_idempotency (
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scope_type, scope_id, idempotency_key))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_config_publish_idempotency_created ON config_publish_idempotency (created_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_token_usage (
                event_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL DEFAULT '',
                doc_id TEXT NOT NULL DEFAULT '',
                pool TEXT NOT NULL DEFAULT 'main',
                task_category TEXT NOT NULL DEFAULT '',
                endpoint_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                api_path TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                api_key_fingerprint TEXT NOT NULL DEFAULT '',
                success BOOLEAN NOT NULL DEFAULT TRUE,
                error_reason TEXT NOT NULL DEFAULT '',
                request_count INTEGER NOT NULL DEFAULT 1,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("ALTER TABLE api_token_usage ADD COLUMN IF NOT EXISTS kb_id TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE api_token_usage ADD COLUMN IF NOT EXISTS doc_id TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE api_token_usage ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_token_usage_tenant_time ON api_token_usage (tenant_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_token_usage_dims ON api_token_usage (tenant_id, api_path, model, api_key_fingerprint, task_category, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_token_usage_kb_day ON api_token_usage (tenant_id, kb_id, created_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_usage_budgets (
                budget_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL DEFAULT '',
                daily_token_limit BIGINT NOT NULL DEFAULT 0,
                daily_cost_limit NUMERIC(18,8) NOT NULL DEFAULT 0,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_api_usage_budgets_scope ON api_usage_budgets (tenant_id, scope_type, scope_value)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_usage_alerts (
                alert_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                budget_id BIGINT NOT NULL,
                day DATE NOT NULL,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL DEFAULT '',
                metric TEXT NOT NULL,
                threshold_value NUMERIC(18,8) NOT NULL,
                actual_value NUMERIC(18,8) NOT NULL,
                level TEXT NOT NULL DEFAULT 'WARN',
                status TEXT NOT NULL DEFAULT 'OPEN',
                message TEXT NOT NULL DEFAULT '',
                first_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                trigger_count INTEGER NOT NULL DEFAULT 1)"""
            )
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_api_usage_alerts_day ON api_usage_alerts (tenant_id, budget_id, day, metric)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_alerts_time ON api_usage_alerts (tenant_id, last_triggered_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_model_pricing_versions (
                version_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT '*',
                model TEXT NOT NULL DEFAULT '*',
                input_per_1k NUMERIC(18,8) NOT NULL DEFAULT 0,
                output_per_1k NUMERIC(18,8) NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                note TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_model_pricing_versions_lookup ON api_model_pricing_versions (tenant_id, model, effective_from DESC)"
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_reconciliation_alerts (
                alert_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                day DATE NOT NULL,
                dimension TEXT NOT NULL DEFAULT 'model',
                dim_value TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT 'WARN',
                threshold_ratio NUMERIC(18,8) NOT NULL DEFAULT 0,
                actual_ratio NUMERIC(18,8) NOT NULL DEFAULT 0,
                estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                priced_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                diff_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OPEN',
                message TEXT NOT NULL DEFAULT '',
                first_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                trigger_count INTEGER NOT NULL DEFAULT 1)"""
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_reconciliation_alerts_day ON api_reconciliation_alerts (tenant_id, day, dimension, dim_value)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_reconciliation_alerts_time ON api_reconciliation_alerts (tenant_id, last_triggered_at DESC)"
            )
            cur.execute("ALTER TABLE api_reconciliation_alerts ADD COLUMN IF NOT EXISTS level TEXT NOT NULL DEFAULT 'WARN'")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_key_lifecycle_alerts (
                alert_id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                day DATE NOT NULL,
                pool TEXT NOT NULL DEFAULT 'main',
                endpoint_id TEXT NOT NULL,
                endpoint_name TEXT NOT NULL DEFAULT '',
                task TEXT NOT NULL DEFAULT '',
                key_fingerprint TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                days_to_expiry INTEGER NULL,
                threshold_days INTEGER NOT NULL DEFAULT 7,
                level TEXT NOT NULL DEFAULT 'WARN',
                status TEXT NOT NULL DEFAULT 'OPEN',
                muted_until TIMESTAMPTZ NULL,
                message TEXT NOT NULL DEFAULT '',
                first_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                acked_at TIMESTAMPTZ NULL,
                resolved_at TIMESTAMPTZ NULL,
                last_triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_notified_at TIMESTAMPTZ NULL,
                trigger_count INTEGER NOT NULL DEFAULT 1)"""
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_key_lifecycle_alerts_day ON api_key_lifecycle_alerts (tenant_id, day, pool, endpoint_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_key_lifecycle_alerts_time ON api_key_lifecycle_alerts (tenant_id, last_triggered_at DESC)"
            )
            cur.execute("ALTER TABLE api_key_lifecycle_alerts ADD COLUMN IF NOT EXISTS muted_until TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE api_key_lifecycle_alerts ADD COLUMN IF NOT EXISTS acked_at TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE api_key_lifecycle_alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE api_key_lifecycle_alerts ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMPTZ NULL")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS api_request_slots (
                endpoint_id TEXT NOT NULL,
                slot_no INTEGER NOT NULL,
                holder_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT '',
                task_category TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (endpoint_id, slot_no))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_request_slots_exp ON api_request_slots (expires_at)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS summary_segment_cache (
                tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                doc_signature TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                segment_hash TEXT NOT NULL,
                overview TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'DONE',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id, doc_signature, segment_index))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_summary_segment_cache_doc ON summary_segment_cache (tenant_id, kb_id, doc_id, doc_signature)")
        self._initialized = True

    def _ensure_indexes(self) -> None:
        """Fail closed unless the explicit schema migration is current."""
        if self._initialized:
            return
        from retrieval.schema_migrations import require_database_current

        require_database_current(self.dsn)
        self._initialized = True

    def _cleanup_api_token_usage(self, retention_days: int = 30) -> None:
        keep_days = max(1, int(retention_days or 30))
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM api_token_usage WHERE created_at < %s", (cutoff,))

    def record_api_token_usage(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        pool: str,
        task_category: str,
        endpoint_id: str,
        provider: str,
        base_url: str,
        api_path: str,
        model: str,
        api_key_fingerprint: str,
        success: bool,
        error_reason: str = "",
        request_count: int = 1,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO api_token_usage (
                    tenant_id,kb_id,doc_id,pool,task_category,endpoint_id,provider,base_url,api_path,model,api_key_fingerprint,
                    success,error_reason,request_count,input_tokens,output_tokens,total_tokens,estimated_cost_usd,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
                (
                    self._strip_nul(tenant_id),
                    self._strip_nul(kb_id),
                    self._strip_nul(doc_id),
                    self._strip_nul(pool),
                    self._strip_nul(task_category),
                    self._strip_nul(endpoint_id),
                    self._strip_nul(provider),
                    self._strip_nul(base_url),
                    self._strip_nul(api_path),
                    self._strip_nul(model),
                    self._strip_nul(api_key_fingerprint),
                    bool(success),
                    self._strip_nul(error_reason)[:400],
                    max(1, int(request_count or 1)),
                    max(0, int(input_tokens or 0)),
                    max(0, int(output_tokens or 0)),
                    max(0, int(total_tokens or 0)),
                    max(0.0, float(estimated_cost_usd or 0.0)),
                ),
            )
        self._cleanup_api_token_usage(retention_days=30)
        try:
            self.evaluate_api_usage_budgets(tenant_id=tenant_id)
        except Exception:
            pass

    def acquire_api_request_slot(
        self,
        *,
        endpoint_id: str,
        holder_id: str,
        tenant_id: str,
        task_category: str,
        max_slots: int = 8,
        ttl_seconds: int = 120,
        wait_seconds: int = 60,
        poll_ms: int = 120,
    ) -> int:
        self._ensure_indexes()
        endpoint = self._strip_nul(endpoint_id)
        holder = self._strip_nul(holder_id)
        if not endpoint or not holder:
            raise ValueError("endpoint_id and holder_id are required")
        slots = max(1, int(max_slots or 8))
        ttl = max(10, int(ttl_seconds or 120))
        deadline = time.time() + max(1, int(wait_seconds or 60))
        poll = max(20, int(poll_ms or 120)) / 1000.0
        while time.time() < deadline:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM api_request_slots WHERE endpoint_id=%s AND expires_at < NOW()", (endpoint,))
                for slot_no in range(slots):
                    cur.execute(
                        """INSERT INTO api_request_slots
                        (endpoint_id,slot_no,holder_id,tenant_id,task_category,created_at,expires_at)
                        VALUES (%s,%s,%s,%s,%s,NOW(),NOW() + (%s || ' seconds')::interval)
                        ON CONFLICT (endpoint_id,slot_no) DO NOTHING""",
                        (
                            endpoint,
                            int(slot_no),
                            holder,
                            self._strip_nul(tenant_id),
                            self._strip_nul(task_category),
                            str(ttl),
                        ),
                    )
                    if cur.rowcount and int(cur.rowcount) > 0:
                        return int(slot_no)
            time.sleep(poll)
        raise TimeoutError(f"api endpoint slot exhausted: {endpoint}")

    def release_api_request_slot(self, *, endpoint_id: str, slot_no: int, holder_id: str) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM api_request_slots WHERE endpoint_id=%s AND slot_no=%s AND holder_id=%s",
                (self._strip_nul(endpoint_id), int(slot_no), self._strip_nul(holder_id)),
            )

    def get_api_token_usage_stats(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        include_all_tenants: bool = False,
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 30))
        cap = max(1, min(int(limit or 200), 1000))
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        self._cleanup_api_token_usage(retention_days=30)

        where_sql = "created_at >= %s"
        params: list = [cutoff]
        if not include_all_tenants:
            where_sql += " AND tenant_id=%s"
            params.append(tenant_id)

        def _query_group(cur, select_dims: str):
            cur.execute(
                f"""SELECT {select_dims},
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens
                FROM api_token_usage
                WHERE {where_sql}
                GROUP BY {select_dims}
                ORDER BY total_tokens DESC, calls DESC
                LIMIT %s""",
                [*params, cap],
            )
            return [dict(r) for r in cur.fetchall()]

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(CASE WHEN success THEN 0 ELSE 1 END),0)::bigint AS failed_calls
                FROM api_token_usage
                WHERE {where_sql}""",
                params,
            )
            totals = dict(cur.fetchone() or {})

            by_api = _query_group(cur, "api_path")
            by_kb = _query_group(cur, "kb_id")
            by_model = _query_group(cur, "model")
            by_key = _query_group(cur, "api_key_fingerprint")
            by_task = _query_group(cur, "task_category")
            by_combo = _query_group(cur, "api_path, model, api_key_fingerprint, task_category")

            cur.execute(
                f"""SELECT
                    TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd
                FROM api_token_usage
                WHERE {where_sql}
                GROUP BY TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')
                ORDER BY day ASC""",
                params,
            )
            by_day = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT
                    TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
                    kb_id,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd
                FROM api_token_usage
                WHERE {where_sql}
                GROUP BY TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD'), kb_id
                ORDER BY day ASC, kb_id ASC
                LIMIT %s""",
                [*params, cap],
            )
            by_day_kb = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT
                    TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
                    model,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd
                FROM api_token_usage
                WHERE {where_sql}
                GROUP BY TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD'), model
                ORDER BY day ASC, model ASC
                LIMIT %s""",
                [*params, cap],
            )
            by_day_model = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT
                    TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
                    task_category,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd
                FROM api_token_usage
                WHERE {where_sql}
                GROUP BY TO_CHAR((created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD'), task_category
                ORDER BY day ASC, task_category ASC
                LIMIT %s""",
                [*params, cap],
            )
            by_day_task = [dict(r) for r in cur.fetchall()]

        return {
            "window_days": keep_days,
            "totals": totals,
            "by_api": by_api,
            "by_kb": by_kb,
            "by_model": by_model,
            "by_api_key": by_key,
            "by_task_category": by_task,
            "by_combination": by_combo,
            "by_day": by_day,
            "by_day_kb": by_day_kb,
            "by_day_model": by_day_model,
            "by_day_task_category": by_day_task,
        }

    def get_summary_segment_cache(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        doc_signature: str,
    ) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,doc_signature,segment_index,segment_hash,overview,summary,status,created_at,updated_at
                FROM summary_segment_cache
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND doc_signature=%s
                ORDER BY segment_index ASC""",
                (tenant_id, kb_id, doc_id, self._strip_nul(doc_signature)),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_summary_segment_cache_latest_by_hash(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
    ) -> dict[str, dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (segment_hash)
                    segment_hash, overview, summary, status, updated_at
                FROM summary_segment_cache
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND status='DONE'
                ORDER BY segment_hash ASC, updated_at DESC""",
                (tenant_id, kb_id, doc_id),
            )
            out: dict[str, dict] = {}
            for row in cur.fetchall():
                h = str((row or {}).get("segment_hash") or "").strip()
                if not h:
                    continue
                out[h] = dict(row)
            return out

    def upsert_summary_segment_cache(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        doc_signature: str,
        segment_index: int,
        segment_hash: str,
        overview: str,
        summary: str,
        status: str = "DONE",
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO summary_segment_cache
                (tenant_id,kb_id,doc_id,doc_signature,segment_index,segment_hash,overview,summary,status,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id,doc_signature,segment_index) DO UPDATE SET
                    segment_hash=EXCLUDED.segment_hash,
                    overview=EXCLUDED.overview,
                    summary=EXCLUDED.summary,
                    status=EXCLUDED.status,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    doc_id,
                    self._strip_nul(doc_signature),
                    int(segment_index),
                    self._strip_nul(segment_hash),
                    self._strip_nul(overview),
                    self._strip_nul(summary),
                    self._strip_nul(status or "DONE"),
                ),
            )

    def list_api_token_usage_events(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        include_all_tenants: bool = False,
        page: int = 1,
        page_size: int = 50,
        api_path: str | None = None,
        model: str | None = None,
        api_key_fingerprint: str | None = None,
        task_category: str | None = None,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 30))
        current_page = max(1, int(page or 1))
        cap = max(1, min(int(page_size or 50), 200))
        offset = (current_page - 1) * cap
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        self._cleanup_api_token_usage(retention_days=30)

        where_parts = ["created_at >= %s"]
        params: list = [cutoff]
        if not include_all_tenants:
            where_parts.append("tenant_id=%s")
            params.append(tenant_id)
        if api_path:
            where_parts.append("api_path=%s")
            params.append(str(api_path))
        if model:
            where_parts.append("model=%s")
            params.append(str(model))
        if api_key_fingerprint:
            where_parts.append("api_key_fingerprint=%s")
            params.append(str(api_key_fingerprint))
        if task_category:
            where_parts.append("task_category=%s")
            params.append(str(task_category))
        where_sql = " AND ".join(where_parts)

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*)::bigint AS c FROM api_token_usage WHERE {where_sql}",
                params,
            )
            total = int((cur.fetchone() or {}).get("c", 0))
            cur.execute(
                f"""SELECT
                    event_id,tenant_id,pool,task_category,endpoint_id,provider,base_url,api_path,model,
                    kb_id,doc_id,api_key_fingerprint,success,error_reason,request_count,input_tokens,output_tokens,total_tokens,estimated_cost_usd,created_at
                FROM api_token_usage
                WHERE {where_sql}
                ORDER BY created_at DESC, event_id DESC
                LIMIT %s OFFSET %s""",
                [*params, cap, offset],
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"page": current_page, "page_size": cap, "total": total, "items": items, "window_days": keep_days}

    def upsert_api_usage_budget(
        self,
        *,
        tenant_id: str,
        scope_type: str,
        scope_value: str,
        daily_token_limit: int = 0,
        daily_cost_limit: float = 0.0,
        enabled: bool = True,
        actor_id: str = "system",
    ) -> dict:
        self._ensure_indexes()
        scope = self._strip_nul(scope_type).upper()
        if scope not in {"TENANT", "KB", "MODEL", "TASK_CATEGORY"}:
            raise ValueError("scope_type must be one of TENANT|KB|MODEL|TASK_CATEGORY")
        value = self._strip_nul(scope_value)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO api_usage_budgets
                (tenant_id,scope_type,scope_value,daily_token_limit,daily_cost_limit,enabled,created_by,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,scope_type,scope_value) DO UPDATE SET
                    daily_token_limit=EXCLUDED.daily_token_limit,
                    daily_cost_limit=EXCLUDED.daily_cost_limit,
                    enabled=EXCLUDED.enabled,
                    created_by=EXCLUDED.created_by,
                    updated_at=NOW()""",
                (
                    self._strip_nul(tenant_id),
                    scope,
                    value,
                    max(0, int(daily_token_limit or 0)),
                    max(0.0, float(daily_cost_limit or 0.0)),
                    bool(enabled),
                    self._strip_nul(actor_id),
                ),
            )
            cur.execute(
                """SELECT budget_id,tenant_id,scope_type,scope_value,daily_token_limit,daily_cost_limit,enabled,created_by,created_at,updated_at
                FROM api_usage_budgets
                WHERE tenant_id=%s AND scope_type=%s AND scope_value=%s""",
                (self._strip_nul(tenant_id), scope, value),
            )
            return dict(cur.fetchone() or {})

    def list_api_usage_budgets(self, *, tenant_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT budget_id,tenant_id,scope_type,scope_value,daily_token_limit,daily_cost_limit,enabled,created_by,created_at,updated_at
                FROM api_usage_budgets
                WHERE tenant_id=%s
                ORDER BY scope_type ASC, scope_value ASC, budget_id ASC""",
                (self._strip_nul(tenant_id),),
            )
            return [dict(r) for r in cur.fetchall()]

    def delete_api_usage_budget(self, *, tenant_id: str, budget_id: int) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM api_usage_budgets WHERE tenant_id=%s AND budget_id=%s",
                (self._strip_nul(tenant_id), int(budget_id)),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def evaluate_api_usage_budgets(self, *, tenant_id: str) -> dict:
        self._ensure_indexes()
        tenant = self._strip_nul(tenant_id)
        utc_day = datetime.now(timezone.utc).date()
        opened: list[dict] = []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT budget_id,tenant_id,scope_type,scope_value,daily_token_limit,daily_cost_limit,enabled
                FROM api_usage_budgets
                WHERE tenant_id=%s AND enabled=TRUE""",
                (tenant,),
            )
            budgets = [dict(r) for r in cur.fetchall()]
            for b in budgets:
                scope_type = str(b.get("scope_type") or "TENANT").upper()
                scope_value = str(b.get("scope_value") or "")
                where_parts = ["tenant_id=%s", "DATE(created_at AT TIME ZONE 'UTC')=%s"]
                params: list = [tenant, utc_day]
                if scope_type == "KB":
                    where_parts.append("kb_id=%s")
                    params.append(scope_value)
                elif scope_type == "MODEL":
                    where_parts.append("model=%s")
                    params.append(scope_value)
                elif scope_type == "TASK_CATEGORY":
                    where_parts.append("task_category=%s")
                    params.append(scope_value)
                where_sql = " AND ".join(where_parts)
                cur.execute(
                    f"""SELECT
                        COALESCE(SUM(total_tokens),0)::bigint AS token_sum,
                        COALESCE(SUM(estimated_cost_usd),0)::numeric(18,8) AS cost_sum
                    FROM api_token_usage
                    WHERE {where_sql}""",
                    params,
                )
                agg = dict(cur.fetchone() or {})
                token_sum = int(agg.get("token_sum") or 0)
                cost_sum = float(agg.get("cost_sum") or 0.0)
                token_limit = int(b.get("daily_token_limit") or 0)
                cost_limit = float(b.get("daily_cost_limit") or 0.0)

                def _upsert_alert(metric: str, threshold: float, actual: float, message: str) -> None:
                    cur.execute(
                        """INSERT INTO api_usage_alerts
                        (tenant_id,budget_id,day,scope_type,scope_value,metric,threshold_value,actual_value,level,status,message,first_triggered_at,last_triggered_at,trigger_count)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'WARN','OPEN',%s,NOW(),NOW(),1)
                        ON CONFLICT (tenant_id,budget_id,day,metric) DO UPDATE SET
                            actual_value=EXCLUDED.actual_value,
                            message=EXCLUDED.message,
                            last_triggered_at=NOW(),
                            trigger_count=api_usage_alerts.trigger_count + 1,
                            status='OPEN'""",
                        (tenant, int(b["budget_id"]), utc_day, scope_type, scope_value, metric, float(threshold), float(actual), self._strip_nul(message)),
                    )
                    opened.append(
                        {
                            "budget_id": int(b["budget_id"]),
                            "scope_type": scope_type,
                            "scope_value": scope_value,
                            "metric": metric,
                            "threshold_value": threshold,
                            "actual_value": actual,
                            "day": str(utc_day),
                            "status": "OPEN",
                        }
                    )

                if token_limit > 0 and token_sum >= token_limit:
                    _upsert_alert("TOKENS", float(token_limit), float(token_sum), f"Daily token budget exceeded: {token_sum}/{token_limit}")
                if cost_limit > 0 and cost_sum >= cost_limit:
                    _upsert_alert("COST_USD", float(cost_limit), float(cost_sum), f"Daily cost budget exceeded: {cost_sum:.6f}/{cost_limit:.6f}")
        return {"day": str(utc_day), "opened": opened, "opened_count": len(opened)}

    def list_api_usage_alerts(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        status: str = "OPEN",
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 30))
        cap = max(1, min(int(limit or 200), 1000))
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days - 1)
        status_norm = self._strip_nul(status).upper()
        where_parts = ["tenant_id=%s", "day >= %s"]
        params: list = [self._strip_nul(tenant_id), cutoff]
        if status_norm and status_norm != "ALL":
            where_parts.append("status=%s")
            params.append(status_norm)
        where_sql = " AND ".join(where_parts)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT alert_id,tenant_id,budget_id,day,scope_type,scope_value,metric,threshold_value,actual_value,
                    level,status,message,first_triggered_at,last_triggered_at,trigger_count
                FROM api_usage_alerts
                WHERE {where_sql}
                ORDER BY last_triggered_at DESC, alert_id DESC
                LIMIT %s""",
                [*params, cap],
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"window_days": keep_days, "items": items, "count": len(items)}

    def update_api_usage_alert_status(self, *, tenant_id: str, alert_id: int, status: str) -> bool:
        self._ensure_indexes()
        next_status = self._strip_nul(status).upper()
        if next_status not in {"OPEN", "ACK", "MUTED", "RESOLVED"}:
            raise ValueError("status must be OPEN|ACK|MUTED|RESOLVED")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE api_usage_alerts SET status=%s WHERE tenant_id=%s AND alert_id=%s",
                (next_status, self._strip_nul(tenant_id), int(alert_id)),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def upsert_api_model_pricing_version(
        self,
        *,
        tenant_id: str,
        model: str,
        input_per_1k: float = 0.0,
        output_per_1k: float = 0.0,
        currency: str = "USD",
        effective_from: str | None = None,
        note: str = "",
        created_by: str = "system",
    ) -> dict:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id).strip() or "*"
        m = self._strip_nul(model).strip() or "*"
        curr = (self._strip_nul(currency).strip().upper() or "USD")[:16]
        created = self._strip_nul(created_by)
        note_safe = self._strip_nul(note)
        effective = self._strip_nul(effective_from or "").strip()
        with self._conn() as conn, conn.cursor() as cur:
            if effective:
                cur.execute(
                    """SELECT version_id FROM api_model_pricing_versions
                    WHERE tenant_id=%s AND model=%s AND effective_from=%s
                    LIMIT 1""",
                    (t, m, effective),
                )
                if cur.fetchone():
                    raise ValueError("pricing version conflict: same tenant/model/effective_from already exists")
                cur.execute(
                    """INSERT INTO api_model_pricing_versions
                    (tenant_id,model,input_per_1k,output_per_1k,currency,effective_from,note,created_by,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    RETURNING version_id,tenant_id,model,input_per_1k,output_per_1k,currency,effective_from,note,created_by,created_at""",
                    (
                        t,
                        m,
                        max(0.0, float(input_per_1k or 0.0)),
                        max(0.0, float(output_per_1k or 0.0)),
                        curr,
                        effective,
                        note_safe,
                        created,
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO api_model_pricing_versions
                    (tenant_id,model,input_per_1k,output_per_1k,currency,note,created_by,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                    RETURNING version_id,tenant_id,model,input_per_1k,output_per_1k,currency,effective_from,note,created_by,created_at""",
                    (
                        t,
                        m,
                        max(0.0, float(input_per_1k or 0.0)),
                        max(0.0, float(output_per_1k or 0.0)),
                        curr,
                        note_safe,
                        created,
                    ),
                )
            return dict(cur.fetchone() or {})

    def list_api_model_pricing_versions(
        self,
        *,
        tenant_id: str,
        model: str = "",
        limit: int = 200,
    ) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit or 200), 2000))
        t = self._strip_nul(tenant_id)
        m = self._strip_nul(model).strip()
        where = ["(tenant_id=%s OR tenant_id='*')"]
        params: list = [t]
        if m:
            where.append("model=%s")
            params.append(m)
        where_sql = " AND ".join(where)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT version_id,tenant_id,model,input_per_1k,output_per_1k,currency,effective_from,note,created_by,created_at
                FROM api_model_pricing_versions
                WHERE {where_sql}
                ORDER BY effective_from DESC, version_id DESC
                LIMIT %s""",
                [*params, cap],
            )
            return [dict(r) for r in cur.fetchall()]

    def delete_api_model_pricing_version(self, *, tenant_id: str, version_id: int) -> bool:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM api_model_pricing_versions WHERE version_id=%s AND (tenant_id=%s OR tenant_id='*')",
                (int(version_id), t),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def get_api_usage_reconciliation_preview(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        include_all_tenants: bool = False,
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cap = max(1, min(int(limit or 200), 2000))
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        where_parts = ["u.created_at >= %s"]
        params: list = [cutoff]
        if not include_all_tenants:
            where_parts.append("u.tenant_id=%s")
            params.append(self._strip_nul(tenant_id))
        where_sql = " AND ".join(where_parts)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                    u.model,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(u.input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(u.output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(u.total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(u.estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd,
                    COALESCE(SUM(
                      (u.input_tokens::numeric / 1000.0) * COALESCE(p.input_per_1k, 0) +
                      (u.output_tokens::numeric / 1000.0) * COALESCE(p.output_per_1k, 0)
                    ),0)::numeric(18,8) AS priced_cost_usd
                FROM api_token_usage u
                LEFT JOIN LATERAL (
                    SELECT input_per_1k, output_per_1k
                    FROM api_model_pricing_versions v
                    WHERE (v.tenant_id = u.tenant_id OR v.tenant_id='*')
                      AND (v.model = u.model OR v.model='*')
                      AND v.effective_from <= u.created_at
                    ORDER BY
                      CASE WHEN v.tenant_id = u.tenant_id THEN 0 ELSE 1 END ASC,
                      CASE WHEN v.model = u.model THEN 0 ELSE 1 END ASC,
                      v.effective_from DESC,
                      v.version_id DESC
                    LIMIT 1
                ) p ON TRUE
                WHERE {where_sql}
                GROUP BY u.model
                ORDER BY ABS(COALESCE(SUM(
                      (u.input_tokens::numeric / 1000.0) * COALESCE(p.input_per_1k, 0) +
                      (u.output_tokens::numeric / 1000.0) * COALESCE(p.output_per_1k, 0)
                    ),0)::numeric(18,8) - COALESCE(SUM(u.estimated_cost_usd),0)::numeric(18,8)) DESC, calls DESC
                LIMIT %s""",
                [*params, cap],
            )
            by_model = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT
                    COALESCE(SUM(u.estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd,
                    COALESCE(SUM(
                      (u.input_tokens::numeric / 1000.0) * COALESCE(p.input_per_1k, 0) +
                      (u.output_tokens::numeric / 1000.0) * COALESCE(p.output_per_1k, 0)
                    ),0)::numeric(18,8) AS priced_cost_usd,
                    COUNT(*)::bigint AS events
                FROM api_token_usage u
                LEFT JOIN LATERAL (
                    SELECT input_per_1k, output_per_1k
                    FROM api_model_pricing_versions v
                    WHERE (v.tenant_id = u.tenant_id OR v.tenant_id='*')
                      AND (v.model = u.model OR v.model='*')
                      AND v.effective_from <= u.created_at
                    ORDER BY
                      CASE WHEN v.tenant_id = u.tenant_id THEN 0 ELSE 1 END ASC,
                      CASE WHEN v.model = u.model THEN 0 ELSE 1 END ASC,
                      v.effective_from DESC,
                      v.version_id DESC
                    LIMIT 1
                ) p ON TRUE
                WHERE {where_sql}""",
                params,
            )
            totals = dict(cur.fetchone() or {})
        est = float(totals.get("estimated_cost_usd") or 0.0)
        priced = float(totals.get("priced_cost_usd") or 0.0)
        return {
            "window_days": keep_days,
            "events": int(totals.get("events") or 0),
            "estimated_cost_usd": est,
            "priced_cost_usd": priced,
            "diff_cost_usd": priced - est,
            "diff_ratio": (0.0 if est == 0 else (priced - est) / est),
            "by_model": by_model,
        }

    def get_api_usage_reconciliation_breakdown(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        include_all_tenants: bool = False,
        dimension: str = "endpoint",
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cap = max(1, min(int(limit or 200), 5000))
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        where_parts = ["u.created_at >= %s"]
        params: list = [cutoff]
        if not include_all_tenants:
            where_parts.append("u.tenant_id=%s")
            params.append(self._strip_nul(tenant_id))
        where_sql = " AND ".join(where_parts)

        dim = str(dimension or "endpoint").strip().lower()
        dim_map: dict[str, tuple[str, str]] = {
            "endpoint": ("u.endpoint_id", "endpoint_id"),
            "task": ("u.task_category", "task_category"),
            "day": ("TO_CHAR((u.created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')", "day"),
            "api_path": ("u.api_path", "api_path"),
            "model": ("u.model", "model"),
        }
        dim_sql, dim_alias = dim_map.get(dim, dim_map["endpoint"])

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                    {dim_sql} AS dim_value,
                    COUNT(*)::bigint AS calls,
                    COALESCE(SUM(u.input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(u.output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(u.total_tokens),0)::bigint AS total_tokens,
                    COALESCE(SUM(u.estimated_cost_usd),0)::numeric(18,8) AS estimated_cost_usd,
                    COALESCE(SUM(
                      (u.input_tokens::numeric / 1000.0) * COALESCE(p.input_per_1k, 0) +
                      (u.output_tokens::numeric / 1000.0) * COALESCE(p.output_per_1k, 0)
                    ),0)::numeric(18,8) AS priced_cost_usd
                FROM api_token_usage u
                LEFT JOIN LATERAL (
                    SELECT input_per_1k, output_per_1k
                    FROM api_model_pricing_versions v
                    WHERE (v.tenant_id = u.tenant_id OR v.tenant_id='*')
                      AND (v.model = u.model OR v.model='*')
                      AND v.effective_from <= u.created_at
                    ORDER BY
                      CASE WHEN v.tenant_id = u.tenant_id THEN 0 ELSE 1 END ASC,
                      CASE WHEN v.model = u.model THEN 0 ELSE 1 END ASC,
                      v.effective_from DESC,
                      v.version_id DESC
                    LIMIT 1
                ) p ON TRUE
                WHERE {where_sql}
                GROUP BY {dim_sql}
                ORDER BY ABS(COALESCE(SUM(
                      (u.input_tokens::numeric / 1000.0) * COALESCE(p.input_per_1k, 0) +
                      (u.output_tokens::numeric / 1000.0) * COALESCE(p.output_per_1k, 0)
                    ),0)::numeric(18,8) - COALESCE(SUM(u.estimated_cost_usd),0)::numeric(18,8)) DESC, calls DESC
                LIMIT %s""",
                [*params, cap],
            )
            rows = [dict(r) for r in cur.fetchall()]

        return {
            "window_days": keep_days,
            "dimension": dim,
            "dimension_alias": dim_alias,
            "items": rows,
            "count": len(rows),
        }

    def evaluate_api_reconciliation_alerts(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        threshold_ratio: float = 0.2,
        min_estimated_cost_usd: float = 1.0,
        dimension: str = "model",
        limit: int = 500,
    ) -> dict:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        day_utc = datetime.now(timezone.utc).date()
        ratio_th = max(0.0, float(threshold_ratio or 0.0))
        min_cost = max(0.0, float(min_estimated_cost_usd or 0.0))
        report = self.get_api_usage_reconciliation_breakdown(
            tenant_id=t,
            days=days,
            include_all_tenants=False,
            dimension=dimension,
            limit=limit,
        )
        opened: list[dict] = []
        with self._conn() as conn, conn.cursor() as cur:
            for row in report.get("items", []):
                dim_value = self._strip_nul(str(row.get("dim_value") or ""))
                est = float(row.get("estimated_cost_usd") or 0.0)
                priced = float(row.get("priced_cost_usd") or 0.0)
                diff = priced - est
                abs_ratio = (abs(diff) / est) if est > 0 else (1.0 if abs(priced) > 0 else 0.0)
                if est < min_cost or abs_ratio < ratio_th:
                    continue
                level = "WARN"
                if ratio_th > 0 and abs_ratio >= ratio_th * 3:
                    level = "CRITICAL"
                elif ratio_th > 0 and abs_ratio >= ratio_th * 2:
                    level = "HIGH"
                msg = f"Reconciliation diff ratio too high: {abs_ratio:.4f} (threshold={ratio_th:.4f}, level={level})"
                cur.execute(
                    """INSERT INTO api_reconciliation_alerts
                    (tenant_id,day,dimension,dim_value,level,threshold_ratio,actual_ratio,estimated_cost_usd,priced_cost_usd,diff_cost_usd,status,message,first_triggered_at,last_triggered_at,trigger_count)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,NOW(),NOW(),1)
                    ON CONFLICT (tenant_id,day,dimension,dim_value) DO UPDATE SET
                        level=EXCLUDED.level,
                        threshold_ratio=EXCLUDED.threshold_ratio,
                        actual_ratio=EXCLUDED.actual_ratio,
                        estimated_cost_usd=EXCLUDED.estimated_cost_usd,
                        priced_cost_usd=EXCLUDED.priced_cost_usd,
                        diff_cost_usd=EXCLUDED.diff_cost_usd,
                        message=EXCLUDED.message,
                        last_triggered_at=NOW(),
                        trigger_count=api_reconciliation_alerts.trigger_count + 1,
                        status='OPEN'""",
                    (
                        t,
                        day_utc,
                        self._strip_nul(str(report.get("dimension") or "model")),
                        dim_value,
                        level,
                        ratio_th,
                        abs_ratio,
                        est,
                        priced,
                        diff,
                        self._strip_nul(msg),
                    ),
                )
                opened.append(
                    {
                        "day": str(day_utc),
                        "dimension": report.get("dimension") or "model",
                        "dim_value": dim_value,
                        "level": level,
                        "threshold_ratio": ratio_th,
                        "actual_ratio": abs_ratio,
                        "estimated_cost_usd": est,
                        "priced_cost_usd": priced,
                        "diff_cost_usd": diff,
                        "status": "OPEN",
                    }
                )
        return {"day": str(day_utc), "opened": opened, "opened_count": len(opened)}

    def list_api_reconciliation_alerts(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        status: str = "OPEN",
        dimension: str = "",
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cap = max(1, min(int(limit or 200), 2000))
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days - 1)
        t = self._strip_nul(tenant_id)
        st = self._strip_nul(status).upper()
        dim = self._strip_nul(dimension).lower()
        where = ["tenant_id=%s", "day >= %s"]
        params: list = [t, cutoff]
        if st and st != "ALL":
            where.append("status=%s")
            params.append(st)
        if dim:
            where.append("lower(dimension)=%s")
            params.append(dim)
        where_sql = " AND ".join(where)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT alert_id,tenant_id,day,dimension,dim_value,level,threshold_ratio,actual_ratio,
                    estimated_cost_usd,priced_cost_usd,diff_cost_usd,status,message,first_triggered_at,last_triggered_at,trigger_count
                FROM api_reconciliation_alerts
                WHERE {where_sql}
                ORDER BY last_triggered_at DESC, alert_id DESC
                LIMIT %s""",
                [*params, cap],
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"window_days": keep_days, "items": items, "count": len(items)}

    def update_api_reconciliation_alert_status(self, *, tenant_id: str, alert_id: int, status: str) -> bool:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        st = self._strip_nul(status).upper()
        if st not in {"OPEN", "ACK", "MUTED", "RESOLVED"}:
            raise ValueError("status must be OPEN|ACK|MUTED|RESOLVED")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE api_reconciliation_alerts SET status=%s WHERE tenant_id=%s AND alert_id=%s",
                (st, t, int(alert_id)),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def evaluate_api_key_lifecycle_alerts(
        self,
        *,
        tenant_id: str,
        items: list[dict],
        warn_days: list[int] | None = None,
        suppress_seconds: int = 600,
        escalate_trigger_count: int = 3,
    ) -> dict:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        day_utc = datetime.now(timezone.utc).date()
        now_dt = datetime.now(timezone.utc)
        thresholds = sorted({max(0, int(x)) for x in (warn_days or [7, 3, 1])}, reverse=True)
        if not thresholds:
            thresholds = [7, 3, 1]
        opened: list[dict] = []
        suppressed: list[dict] = []
        suppress_seconds = max(0, int(suppress_seconds or 0))
        escalate_trigger_count = max(1, int(escalate_trigger_count or 1))

        def _raise_level(level: str) -> str:
            lv = str(level or "WARN").upper()
            if lv == "WARN":
                return "HIGH"
            if lv == "HIGH":
                return "CRITICAL"
            return "CRITICAL"

        with self._conn() as conn, conn.cursor() as cur:
            for one in items:
                endpoint_id = self._strip_nul(str(one.get("endpoint_id") or ""))
                if not endpoint_id:
                    continue
                pool = self._strip_nul(str(one.get("pool") or "main"))
                endpoint_name = self._strip_nul(str(one.get("name") or ""))
                task = self._strip_nul(str(one.get("task") or ""))
                fingerprint = self._strip_nul(str(one.get("key_fingerprint") or ""))
                expires_at = self._strip_nul(str(one.get("expires_at") or ""))
                status_text = str(one.get("status") or "").upper()
                days_left_raw = one.get("days_to_expiry")
                days_left = None if days_left_raw is None else int(days_left_raw)

                trigger = False
                level = "WARN"
                threshold_days = thresholds[0]
                if status_text == "EXPIRED":
                    trigger = True
                    level = "CRITICAL"
                    threshold_days = 0
                elif status_text == "EXPIRING" and days_left is not None:
                    candidate = [d for d in thresholds if days_left <= d]
                    if candidate:
                        trigger = True
                        threshold_days = min(candidate)
                        if days_left <= 1:
                            level = "CRITICAL"
                        elif days_left <= 3:
                            level = "HIGH"
                        else:
                            level = "WARN"
                if not trigger:
                    continue
                if status_text == "EXPIRED":
                    msg = f"API key expired: {pool}/{endpoint_id}"
                else:
                    msg = f"API key expiring soon ({days_left} day): {pool}/{endpoint_id}"
                cur.execute(
                    """SELECT alert_id,status,level,trigger_count,last_triggered_at,muted_until
                    FROM api_key_lifecycle_alerts
                    WHERE tenant_id=%s AND day=%s AND pool=%s AND endpoint_id=%s
                    LIMIT 1""",
                    (t, day_utc, pool, endpoint_id),
                )
                existing = dict(cur.fetchone() or {})
                if not existing:
                    cur.execute(
                        """INSERT INTO api_key_lifecycle_alerts
                        (tenant_id,day,pool,endpoint_id,endpoint_name,task,key_fingerprint,expires_at,days_to_expiry,threshold_days,level,status,message,first_triggered_at,last_triggered_at,trigger_count)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,NOW(),NOW(),1)
                        RETURNING alert_id""",
                        (
                            t,
                            day_utc,
                            pool,
                            endpoint_id,
                            endpoint_name,
                            task,
                            fingerprint,
                            expires_at,
                            days_left,
                            threshold_days,
                            level,
                            self._strip_nul(msg),
                        ),
                    )
                    inserted = dict(cur.fetchone() or {})
                    opened.append(
                        {
                            "alert_id": int(inserted.get("alert_id") or 0),
                            "day": str(day_utc),
                            "pool": pool,
                            "endpoint_id": endpoint_id,
                            "task": task,
                            "level": level,
                            "status": "OPEN",
                            "days_to_expiry": days_left,
                            "threshold_days": threshold_days,
                        }
                    )
                    continue

                trigger_count = int(existing.get("trigger_count") or 0) + 1
                level_next = level
                if trigger_count >= escalate_trigger_count:
                    level_next = _raise_level(level_next)
                existing_last = existing.get("last_triggered_at")
                suppressed_hit = False
                if suppress_seconds > 0 and existing_last is not None:
                    try:
                        dt = existing_last if isinstance(existing_last, datetime) else datetime.fromisoformat(str(existing_last).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        suppressed_hit = (now_dt - dt).total_seconds() < suppress_seconds
                    except Exception:
                        suppressed_hit = False
                muted_until = existing.get("muted_until")
                muted_active = False
                if str(existing.get("status") or "").upper() == "MUTED" and muted_until is not None:
                    try:
                        mu = muted_until if isinstance(muted_until, datetime) else datetime.fromisoformat(str(muted_until).replace("Z", "+00:00"))
                        if mu.tzinfo is None:
                            mu = mu.replace(tzinfo=timezone.utc)
                        muted_active = mu > now_dt
                    except Exception:
                        muted_active = False
                next_status = "MUTED" if muted_active else "OPEN"
                cur.execute(
                    """UPDATE api_key_lifecycle_alerts
                    SET endpoint_name=%s,
                        task=%s,
                        key_fingerprint=%s,
                        expires_at=%s,
                        days_to_expiry=%s,
                        threshold_days=%s,
                        level=%s,
                        message=%s,
                        last_triggered_at=NOW(),
                        trigger_count=%s,
                        status=%s
                    WHERE alert_id=%s""",
                    (
                        endpoint_name,
                        task,
                        fingerprint,
                        expires_at,
                        days_left,
                        threshold_days,
                        level_next,
                        self._strip_nul(msg),
                        trigger_count,
                        next_status,
                        int(existing.get("alert_id")),
                    ),
                )
                event_payload = {
                    "alert_id": int(existing.get("alert_id") or 0),
                    "day": str(day_utc),
                    "pool": pool,
                    "endpoint_id": endpoint_id,
                    "task": task,
                    "level": level_next,
                    "status": next_status,
                    "days_to_expiry": days_left,
                    "threshold_days": threshold_days,
                    "trigger_count": trigger_count,
                }
                if suppressed_hit:
                    suppressed.append(event_payload)
                elif next_status == "OPEN":
                    opened.append(event_payload)
                else:
                    suppressed.append(event_payload)
        return {
            "day": str(day_utc),
            "opened": opened,
            "opened_count": len(opened),
            "suppressed": suppressed,
            "suppressed_count": len(suppressed),
        }

    def list_api_key_lifecycle_alerts(
        self,
        *,
        tenant_id: str,
        days: int = 30,
        status: str = "OPEN",
        pool: str = "",
        level: str = "",
        limit: int = 200,
    ) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cap = max(1, min(int(limit or 200), 2000))
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days - 1)
        t = self._strip_nul(tenant_id)
        st = self._strip_nul(status).upper()
        pool_v = self._strip_nul(pool).lower()
        lvl = self._strip_nul(level).upper()
        where = ["tenant_id=%s", "day >= %s"]
        params: list = [t, cutoff]
        if st and st != "ALL":
            where.append("status=%s")
            params.append(st)
        if pool_v:
            where.append("lower(pool)=%s")
            params.append(pool_v)
        if lvl:
            where.append("upper(level)=%s")
            params.append(lvl)
        where_sql = " AND ".join(where)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT alert_id,tenant_id,day,pool,endpoint_id,endpoint_name,task,key_fingerprint,expires_at,days_to_expiry,threshold_days,
                    level,status,muted_until,message,first_triggered_at,acked_at,resolved_at,last_triggered_at,last_notified_at,trigger_count
                FROM api_key_lifecycle_alerts
                WHERE {where_sql}
                ORDER BY last_triggered_at DESC, alert_id DESC
                LIMIT %s""",
                [*params, cap],
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"window_days": keep_days, "items": items, "count": len(items)}

    def update_api_key_lifecycle_alert_status(self, *, tenant_id: str, alert_id: int, status: str, muted_seconds: int = 0) -> bool:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        st = self._strip_nul(status).upper()
        if st not in {"OPEN", "ACK", "MUTED", "RESOLVED"}:
            raise ValueError("status must be OPEN|ACK|MUTED|RESOLVED")
        with self._conn() as conn, conn.cursor() as cur:
            if st == "MUTED" and int(muted_seconds or 0) > 0:
                cur.execute(
                    "UPDATE api_key_lifecycle_alerts SET status=%s, muted_until=(NOW() + (%s || ' seconds')::interval) WHERE tenant_id=%s AND alert_id=%s",
                    (st, int(muted_seconds), t, int(alert_id)),
                )
            elif st == "ACK":
                cur.execute(
                    "UPDATE api_key_lifecycle_alerts SET status=%s, muted_until=NULL, acked_at=COALESCE(acked_at, NOW()) WHERE tenant_id=%s AND alert_id=%s",
                    (st, t, int(alert_id)),
                )
            elif st == "RESOLVED":
                cur.execute(
                    "UPDATE api_key_lifecycle_alerts SET status=%s, muted_until=NULL, resolved_at=COALESCE(resolved_at, NOW()) WHERE tenant_id=%s AND alert_id=%s",
                    (st, t, int(alert_id)),
                )
            elif st == "OPEN":
                cur.execute(
                    "UPDATE api_key_lifecycle_alerts SET status=%s, muted_until=NULL WHERE tenant_id=%s AND alert_id=%s",
                    (st, t, int(alert_id)),
                )
            else:
                cur.execute(
                    "UPDATE api_key_lifecycle_alerts SET status=%s WHERE tenant_id=%s AND alert_id=%s",
                    (st, t, int(alert_id)),
                )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def mark_api_key_lifecycle_alerts_notified(
        self,
        *,
        tenant_id: str,
        alert_ids: list[int],
        cooldown_seconds: int = 300,
    ) -> list[int]:
        self._ensure_indexes()
        t = self._strip_nul(tenant_id)
        ids = [int(x) for x in (alert_ids or []) if int(x) > 0]
        if not ids:
            return []
        cooldown = max(0, int(cooldown_seconds or 0))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE api_key_lifecycle_alerts
                SET last_notified_at=NOW()
                WHERE tenant_id=%s
                  AND alert_id = ANY(%s)
                  AND (
                    last_notified_at IS NULL
                    OR last_notified_at < (NOW() - (%s || ' seconds')::interval)
                  )
                RETURNING alert_id""",
                (t, ids, cooldown),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return [int(r.get("alert_id") or 0) for r in rows if int(r.get("alert_id") or 0) > 0]

    def list_api_key_lifecycle_alert_trend(self, *, tenant_id: str, days: int = 30) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days - 1)
        t = self._strip_nul(tenant_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                    day,
                    COUNT(*)::bigint AS total,
                    COUNT(*) FILTER (WHERE level='WARN')::bigint AS warn_count,
                    COUNT(*) FILTER (WHERE level='HIGH')::bigint AS high_count,
                    COUNT(*) FILTER (WHERE level='CRITICAL')::bigint AS critical_count,
                    COUNT(*) FILTER (WHERE status='OPEN')::bigint AS open_count,
                    COUNT(*) FILTER (WHERE status='RESOLVED')::bigint AS resolved_count
                FROM api_key_lifecycle_alerts
                WHERE tenant_id=%s AND day >= %s
                GROUP BY day
                ORDER BY day ASC""",
                (t, cutoff),
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"window_days": keep_days, "items": items, "count": len(items)}

    def get_api_key_lifecycle_alert_sla(self, *, tenant_id: str, days: int = 30, limit: int = 20) -> dict:
        self._ensure_indexes()
        keep_days = max(1, min(int(days or 30), 90))
        cap = max(1, min(int(limit or 20), 200))
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days - 1)
        t = self._strip_nul(tenant_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                    COUNT(*)::bigint AS total,
                    COUNT(*) FILTER (WHERE status='OPEN')::bigint AS open_count,
                    COUNT(*) FILTER (WHERE status='ACK')::bigint AS ack_count,
                    COUNT(*) FILTER (WHERE status='MUTED')::bigint AS muted_count,
                    COUNT(*) FILTER (WHERE status='RESOLVED')::bigint AS resolved_count,
                    AVG(EXTRACT(EPOCH FROM (acked_at - first_triggered_at))) FILTER (WHERE acked_at IS NOT NULL)::numeric(18,4) AS avg_ack_seconds,
                    AVG(EXTRACT(EPOCH FROM (resolved_at - first_triggered_at))) FILTER (WHERE resolved_at IS NOT NULL)::numeric(18,4) AS avg_resolve_seconds
                FROM api_key_lifecycle_alerts
                WHERE tenant_id=%s AND day >= %s""",
                (t, cutoff),
            )
            summary = dict(cur.fetchone() or {})
            cur.execute(
                """SELECT
                    alert_id,day,pool,endpoint_id,level,status,message,first_triggered_at,last_triggered_at,
                    EXTRACT(EPOCH FROM (NOW() - first_triggered_at))::bigint AS age_seconds
                FROM api_key_lifecycle_alerts
                WHERE tenant_id=%s AND day >= %s AND status IN ('OPEN','ACK','MUTED')
                ORDER BY age_seconds DESC, last_triggered_at DESC
                LIMIT %s""",
                (t, cutoff, cap),
            )
            top_overdue = [dict(r) for r in cur.fetchall()]
        return {
            "window_days": keep_days,
            "summary": summary,
            "top_overdue": top_overdue,
            "count": len(top_overdue),
        }

    def ensure_kb(self, tenant_id: str, kb_id: str, name: str | None = None) -> None:
        self._ensure_indexes()
        default_visibility = "SHARED_ALL" if str(tenant_id or "").strip() == "platform" else "PRIVATE"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id, kb_id, name, visibility, summary, status, stats, created_at, updated_at)
                VALUES (%s, %s, %s, %s, '', 'ACTIVE', '{}'::jsonb, NOW(), NOW())
                ON CONFLICT (tenant_id, kb_id) DO NOTHING""",
                (tenant_id, kb_id, name or kb_id, default_visibility),
            )

    def get_kb(self, tenant_id: str, kb_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,visibility,summary,frozen,summary_embedding_json,summary_dirty,summary_updated_at,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_kbs(self, tenant_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,visibility,summary,frozen,summary_embedding_json,summary_dirty,summary_updated_at,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s ORDER BY created_at ASC",
                (tenant_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_accessible_kbs(self, tenant_id: str) -> list[dict]:
        self._ensure_indexes()
        safe_tenant = self._strip_nul(tenant_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH candidates AS (
                  SELECT
                    kb.tenant_id AS owner_tenant_id,
                    kb.kb_id,
                    kb.name,
                    kb.visibility,
                    kb.summary,
                    kb.frozen,
                    kb.summary_embedding_json,
                    kb.summary_dirty,
                    kb.summary_updated_at,
                    kb.status,
                    kb.stats,
                    kb.created_at,
                    kb.updated_at,
                    1 AS access_priority,
                    'OWNER'::text AS access_mode
                  FROM knowledge_bases kb
                  WHERE kb.tenant_id=%s
                  UNION ALL
                  SELECT
                    kb.tenant_id AS owner_tenant_id,
                    kb.kb_id,
                    kb.name,
                    kb.visibility,
                    kb.summary,
                    kb.frozen,
                    kb.summary_embedding_json,
                    kb.summary_dirty,
                    kb.summary_updated_at,
                    kb.status,
                    kb.stats,
                    kb.created_at,
                    kb.updated_at,
                    2 AS access_priority,
                    'BOUND'::text AS access_mode
                  FROM kb_tenant_bindings b
                  JOIN knowledge_bases kb
                    ON kb.tenant_id=b.owner_tenant_id AND kb.kb_id=b.kb_id
                  WHERE b.target_tenant_id=%s
                  UNION ALL
                  SELECT
                    kb.tenant_id AS owner_tenant_id,
                    kb.kb_id,
                    kb.name,
                    kb.visibility,
                    kb.summary,
                    kb.frozen,
                    kb.summary_embedding_json,
                    kb.summary_dirty,
                    kb.summary_updated_at,
                    kb.status,
                    kb.stats,
                    kb.created_at,
                    kb.updated_at,
                    3 AS access_priority,
                    'SHARED_ALL'::text AS access_mode
                  FROM knowledge_bases kb
                  WHERE kb.visibility='SHARED_ALL'
                )
                SELECT DISTINCT ON (kb_id)
                  owner_tenant_id AS tenant_id,
                  kb_id,name,visibility,summary,frozen,summary_embedding_json,summary_dirty,summary_updated_at,status,stats,created_at,updated_at,
                  access_mode,access_priority
                FROM candidates
                ORDER BY kb_id, access_priority ASC, updated_at DESC
                """,
                (safe_tenant, safe_tenant),
            )
            return [dict(row) for row in cur.fetchall()]

    def resolve_kb_access_tenant(self, tenant_id: str, kb_id: str) -> str | None:
        self._ensure_indexes()
        safe_tenant = self._strip_nul(tenant_id)
        safe_kb = self._strip_nul(kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH candidates AS (
                  SELECT tenant_id AS owner_tenant_id, 1 AS access_priority
                  FROM knowledge_bases
                  WHERE tenant_id=%s AND kb_id=%s
                  UNION ALL
                  SELECT owner_tenant_id, 2 AS access_priority
                  FROM kb_tenant_bindings
                  WHERE target_tenant_id=%s AND kb_id=%s
                  UNION ALL
                  SELECT tenant_id AS owner_tenant_id, 3 AS access_priority
                  FROM knowledge_bases
                  WHERE kb_id=%s AND visibility='SHARED_ALL'
                )
                SELECT owner_tenant_id
                FROM candidates
                ORDER BY access_priority ASC
                LIMIT 1
                """,
                (safe_tenant, safe_kb, safe_tenant, safe_kb, safe_kb),
            )
            row = cur.fetchone() or {}
            owner = self._strip_nul(row.get("owner_tenant_id"))
            return owner or None

    def _resolve_unique_kb_name(self, tenant_id: str, requested_name: str, exclude_kb_id: str | None = None) -> str:
        self._ensure_indexes()
        base_name = (requested_name or "").strip() or "未命名知识库"
        with self._conn() as conn, conn.cursor() as cur:
            if exclude_kb_id:
                cur.execute("SELECT name FROM knowledge_bases WHERE tenant_id=%s AND kb_id<>%s", (tenant_id, exclude_kb_id))
            else:
                cur.execute("SELECT name FROM knowledge_bases WHERE tenant_id=%s", (tenant_id,))
            existing = {str(row["name"] or "").strip().lower() for row in cur.fetchall() if str(row["name"] or "").strip()}
        if base_name.lower() not in existing:
            return base_name
        stem = re.sub(r"\s\(\d+\)$", "", base_name).strip() or "未命名知识库"
        idx = 2
        while True:
            candidate = f"{stem} ({idx})"
            if candidate.lower() not in existing:
                return candidate
            idx += 1

    def create_kb(self, tenant_id: str, kb_id: str, name: str, summary: str = "") -> dict:
        self._ensure_indexes()
        final_name = self._resolve_unique_kb_name(tenant_id, name)
        clean_summary = self._strip_nul(summary or "")
        default_visibility = "SHARED_ALL" if str(tenant_id or "").strip() == "platform" else "PRIVATE"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id,kb_id,name,visibility,summary,status,summary_dirty,summary_updated_at,stats,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s,'{}'::jsonb,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id) DO UPDATE SET
                    name=EXCLUDED.name, visibility=EXCLUDED.visibility, summary=EXCLUDED.summary,
                    summary_dirty=EXCLUDED.summary_dirty,
                    summary_updated_at=EXCLUDED.summary_updated_at,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    final_name,
                    default_visibility,
                    clean_summary,
                    (False if clean_summary.strip() else True),
                    (datetime.now(timezone.utc) if clean_summary.strip() else None),
                ),
            )
        self.recompute_kb_stats(tenant_id, kb_id)
        item = self.get_kb(tenant_id, kb_id)
        if item:
            item["name_adjusted"] = final_name != name
        return item or {"tenant_id": tenant_id, "kb_id": kb_id, "name": final_name, "summary": summary or "", "name_adjusted": final_name != name}

    def rename_kb(self, tenant_id: str, kb_id: str, name: str) -> dict | None:
        self._ensure_indexes()
        final_name = self._resolve_unique_kb_name(tenant_id, name, exclude_kb_id=kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE knowledge_bases SET name=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s", (final_name, tenant_id, kb_id))
            if cur.rowcount == 0:
                return None
        item = self.get_kb(tenant_id, kb_id)
        if item:
            item["name_adjusted"] = final_name != name
        return item

    def delete_kb(self, tenant_id: str, kb_id: str, *, delete_metadata: bool = True) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            exists = cur.fetchone() is not None
            if not exists:
                return False
            cur.execute(
                """SELECT doc_id,source_object_bucket,source_object_key,extracted_object_key
                FROM documents WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            document_objects = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT collection_name FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND collection_name<>''""",
                (tenant_id, kb_id),
            )
            revision_collections = {str(row.get("collection_name") or "") for row in cur.fetchall()}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM kb_tenant_bindings WHERE owner_tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM summary_segment_cache WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM task_dedup_locks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM task_batches WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM rag_index_manifests WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM task_events WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM tasks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            if delete_metadata:
                cur.execute("DELETE FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
                deleted = cur.rowcount > 0
            else:
                deleted = True
        if not deleted:
            return False
        collection_names = revision_collections | {self._collection_name(tenant_id, kb_id)}
        for collection_name in sorted(name for name in collection_names if name):
            try:
                if self.qdrant.collection_exists(collection_name=collection_name):
                    self.qdrant.delete_collection(collection_name=collection_name)
            except Exception as exc:
                self.enqueue_cross_store_repair(
                    store_name="QDRANT",
                    operation="DELETE_COLLECTION",
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    error_message=str(exc),
                    payload={"collection_name": collection_name},
                )
        for item in document_objects:
            bucket = str(item.get("source_object_bucket") or "")
            keys = {str(item.get("source_object_key") or ""), str(item.get("extracted_object_key") or "")}
            for key in sorted(value for value in keys if value):
                try:
                    self._object_store().delete_object(bucket, key)
                except Exception as exc:
                    self.enqueue_cross_store_repair(
                        store_name="MINIO",
                        operation="DELETE_OBJECT",
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        doc_id=str(item.get("doc_id") or ""),
                        error_message=str(exc),
                        payload={"bucket": bucket, "key": key},
                    )
        return deleted

    def set_kb_visibility(self, tenant_id: str, kb_id: str, visibility: str) -> dict | None:
        self._ensure_indexes()
        target = self._strip_nul(visibility).upper()
        if target not in {"PRIVATE", "SHARED_ALL"}:
            raise ValueError("visibility must be PRIVATE|SHARED_ALL")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_bases SET visibility=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s",
                (target, tenant_id, kb_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_kb(tenant_id, kb_id)

    def list_kb_bindings(self, owner_tenant_id: str, kb_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT owner_tenant_id,kb_id,target_tenant_id,created_by,created_at,updated_at
                FROM kb_tenant_bindings
                WHERE owner_tenant_id=%s AND kb_id=%s
                ORDER BY updated_at DESC""",
                (owner_tenant_id, kb_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def upsert_kb_binding(self, owner_tenant_id: str, kb_id: str, target_tenant_id: str, actor_id: str) -> dict | None:
        self._ensure_indexes()
        target = self._strip_nul(target_tenant_id)
        if not target:
            raise ValueError("target_tenant_id required")
        if target == owner_tenant_id:
            raise ValueError("owner tenant does not need explicit binding")
        kb = self.get_kb(owner_tenant_id, kb_id)
        if not kb:
            return None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_tenant_bindings(owner_tenant_id,kb_id,target_tenant_id,created_by,created_at,updated_at)
                VALUES (%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (owner_tenant_id,kb_id,target_tenant_id) DO UPDATE SET
                  created_by=EXCLUDED.created_by,updated_at=NOW()""",
                (owner_tenant_id, kb_id, target, self._strip_nul(actor_id or "system")),
            )
            cur.execute(
                """SELECT owner_tenant_id,kb_id,target_tenant_id,created_by,created_at,updated_at
                FROM kb_tenant_bindings
                WHERE owner_tenant_id=%s AND kb_id=%s AND target_tenant_id=%s""",
                (owner_tenant_id, kb_id, target),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_kb_binding(self, owner_tenant_id: str, kb_id: str, target_tenant_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_tenant_bindings WHERE owner_tenant_id=%s AND kb_id=%s AND target_tenant_id=%s",
                (owner_tenant_id, kb_id, self._strip_nul(target_tenant_id)),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def set_kb_frozen(self, tenant_id: str, kb_id: str, frozen: bool) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_bases SET frozen=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s",
                (bool(frozen), tenant_id, kb_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_kb(tenant_id, kb_id)

    def save_document(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        filename: str,
        raw_text: str,
        chunk_method: str = "sentence",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        chunk_delimiter: str | None = None,
    ) -> None:
        self._ensure_indexes()
        self.ensure_kb(tenant_id, kb_id)
        filename = self._strip_nul(filename)
        raw_text = self._strip_nul(raw_text)
        chunk_method = (chunk_method or "sentence").strip().lower()
        chunk_size = int(chunk_size or 500)
        chunk_overlap = int(50 if chunk_overlap is None else chunk_overlap)
        chunk_delimiter = self._strip_nul(chunk_delimiter)
        text_bytes = len(raw_text.encode("utf-8"))
        raw_text_to_store = raw_text
        raw_text_preview = raw_text[:2000]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (
                    tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,tags_json,summary_overview,summary_text,summary_embedding_json,summary_dirty,text_size_bytes,word_count,
                    status,chunk_count,embedded_count,embedding_dirty,frozen,chunk_method,chunk_size,chunk_overlap,chunk_delimiter,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'[]'::jsonb,'','','[]'::jsonb,TRUE,%s,%s,'UPLOADED',0,0,TRUE,FALSE,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id) DO UPDATE SET
                    filename=EXCLUDED.filename, raw_text=EXCLUDED.raw_text, raw_text_preview=EXCLUDED.raw_text_preview,
                    summary_overview='', summary_text='', summary_embedding_json='[]'::jsonb, summary_dirty=TRUE,
                    text_size_bytes=EXCLUDED.text_size_bytes, word_count=EXCLUDED.word_count,
                    status='UPLOADED', chunk_count=0, embedded_count=0, embedding_dirty=TRUE,
                    chunk_method=EXCLUDED.chunk_method, chunk_size=EXCLUDED.chunk_size,
                    chunk_overlap=EXCLUDED.chunk_overlap, chunk_delimiter=EXCLUDED.chunk_delimiter,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    doc_id,
                    filename,
                    raw_text_to_store,
                    raw_text_preview,
                    text_bytes,
                    len(raw_text.split()),
                    chunk_method,
                    chunk_size,
                    chunk_overlap,
                    chunk_delimiter or "",
                ),
            )
        self.recompute_kb_stats(tenant_id, kb_id)

    def set_document_chunk_config(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunk_method: str,
        chunk_size: int,
        chunk_overlap: int,
        chunk_delimiter: str | None = None,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET chunk_method=%s, chunk_size=%s, chunk_overlap=%s, chunk_delimiter=%s, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (
                    (chunk_method or "sentence").strip().lower(),
                    int(chunk_size or 500),
                    int(50 if chunk_overlap is None else chunk_overlap),
                    self._strip_nul(chunk_delimiter),
                    tenant_id,
                    kb_id,
                    doc_id,
                ),
            )

    def set_document_tags(self, tenant_id: str, kb_id: str, doc_id: str, tags: list[str]) -> bool:
        self._ensure_indexes()
        normalized: list[str] = []
        seen: set[str] = set()
        for t in tags or []:
            val = self._strip_nul(str(t or "").strip())
            if not val:
                continue
            key = val.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(val)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET tags_json=%s::jsonb, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (json.dumps(normalized, ensure_ascii=False), tenant_id, kb_id, doc_id),
            )
            return cur.rowcount > 0

    def add_document_tags(self, tenant_id: str, kb_id: str, doc_id: str, tags: list[str]) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tags_json FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (tenant_id, kb_id, doc_id),
            )
            row = cur.fetchone() or {}
            existing = row.get("tags_json") or []
            merged: list[str] = []
            seen: set[str] = set()
            for t in list(existing) + list(tags or []):
                val = self._strip_nul(str(t or "").strip())
                if not val:
                    continue
                key = val.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(val)
            cur.execute(
                """UPDATE documents
                SET tags_json=%s::jsonb, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (json.dumps(merged, ensure_ascii=False), tenant_id, kb_id, doc_id),
            )
            return cur.rowcount > 0

    def move_document(self, tenant_id: str, from_kb_id: str, to_kb_id: str, doc_id: str) -> bool:
        self._ensure_indexes()
        if from_kb_id == to_kb_id:
            return True
        collection_names = [
            self._collection_name(tenant_id, from_kb_id),
            self._collection_name(tenant_id, to_kb_id),
        ]
        with self._qdrant_collection_locks(collection_names):
            return self._move_document_locked(tenant_id, from_kb_id, to_kb_id, doc_id)

    def _move_document_locked(self, tenant_id: str, from_kb_id: str, to_kb_id: str, doc_id: str) -> bool:
        self.ensure_kb(tenant_id, to_kb_id)
        doc = self.get_document(tenant_id, from_kb_id, doc_id)
        if not doc:
            target_doc = self.get_document(tenant_id, to_kb_id, doc_id)
            if not target_doc:
                return False
            repair = self.find_document_move_repair(tenant_id, from_kb_id, to_kb_id, doc_id)
            if not repair:
                original_status = str(target_doc.get("status") or "INDEXED")
                if original_status == "MOVING":
                    original_status = "INDEXED"
                repair_id = self.enqueue_cross_store_repair(
                    store_name="QDRANT",
                    operation="MOVE_DOCUMENT_POINTS",
                    tenant_id=tenant_id,
                    kb_id=from_kb_id,
                    doc_id=doc_id,
                    payload={
                        "source_kb_id": from_kb_id,
                        "target_kb_id": to_kb_id,
                        "original_status": original_status,
                    },
                )
            else:
                repair_id = int(repair["repair_id"])
            if not self.repair_cross_store_operation(repair_id, _collection_lock_held=True):
                raise RuntimeError(f"document move repair pending: repair_id={repair_id}")
            return True
        original_status = self._strip_nul(str(doc.get("status") or "UPLOADED"))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_id,chunk_index,text,frozen,embedding_json,metadata,
                    parent_id,section_path,block_type,page_start,page_end
                FROM chunks
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s
                ORDER BY chunk_index ASC""",
                (tenant_id, from_kb_id, doc_id),
            )
            chunks = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """SELECT parent_id,parent_index,text,section_path,page_start,page_end,metadata,index_revision
                FROM chunk_parents
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY parent_index ASC""",
                (tenant_id, from_kb_id, doc_id),
            )
            parents = [dict(r) for r in cur.fetchall()]
        drafts = self.list_chunk_drafts(tenant_id, from_kb_id, doc_id)
        with self._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (
                    tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,tags_json,
                    summary_overview,summary_text,summary_embedding_json,summary_dirty,text_size_bytes,word_count,
                    status,chunk_count,embedded_count,embedding_dirty,frozen,
                    chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                    source_object_bucket,source_object_key,source_object_sha256,source_object_size_bytes,
                    source_content_type,extracted_object_key,extracted_text_sha256,extracted_object_size_bytes,
                    created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id) DO UPDATE SET
                    filename=EXCLUDED.filename,
                    raw_text=EXCLUDED.raw_text,
                    raw_text_preview=EXCLUDED.raw_text_preview,
                    tags_json=EXCLUDED.tags_json,
                    summary_overview=EXCLUDED.summary_overview,
                    summary_text=EXCLUDED.summary_text,
                    summary_embedding_json=EXCLUDED.summary_embedding_json,
                    summary_dirty=EXCLUDED.summary_dirty,
                    text_size_bytes=EXCLUDED.text_size_bytes,
                    word_count=EXCLUDED.word_count,
                    status=EXCLUDED.status,
                    chunk_count=EXCLUDED.chunk_count,
                    embedded_count=EXCLUDED.embedded_count,
                    embedding_dirty=EXCLUDED.embedding_dirty,
                    frozen=EXCLUDED.frozen,
                    chunk_method=EXCLUDED.chunk_method,
                    chunk_size=EXCLUDED.chunk_size,
                    chunk_overlap=EXCLUDED.chunk_overlap,
                    chunk_delimiter=EXCLUDED.chunk_delimiter,
                    source_object_bucket=EXCLUDED.source_object_bucket,
                    source_object_key=EXCLUDED.source_object_key,
                    source_object_sha256=EXCLUDED.source_object_sha256,
                    source_object_size_bytes=EXCLUDED.source_object_size_bytes,
                    source_content_type=EXCLUDED.source_content_type,
                    extracted_object_key=EXCLUDED.extracted_object_key,
                    extracted_text_sha256=EXCLUDED.extracted_text_sha256,
                    extracted_object_size_bytes=EXCLUDED.extracted_object_size_bytes,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    to_kb_id,
                    doc_id,
                    self._strip_nul(doc.get("filename", "")),
                    self._strip_nul(doc.get("raw_text", "")),
                    self._strip_nul(doc.get("raw_text_preview", "")),
                    json.dumps(doc.get("tags_json") or [], ensure_ascii=False),
                    self._strip_nul(doc.get("summary_overview", "")),
                    self._strip_nul(doc.get("summary_text", "")),
                    json.dumps(doc.get("summary_embedding_json") or [], ensure_ascii=False),
                    (False if self._strip_nul(doc.get("summary_text", "")).strip() else bool(doc.get("summary_dirty", True))),
                    int(doc.get("text_size_bytes") or 0),
                    int(doc.get("word_count") or 0),
                    "MOVING",
                    int(doc.get("chunk_count") or 0),
                    int(doc.get("embedded_count") or 0),
                    bool(doc.get("embedding_dirty", True)),
                    bool(doc.get("frozen", False)),
                    self._strip_nul(doc.get("chunk_method", "sentence")),
                    int(doc.get("chunk_size") or 500),
                    int(50 if doc.get("chunk_overlap") is None else doc.get("chunk_overlap")),
                    self._strip_nul(doc.get("chunk_delimiter", "")),
                    self._strip_nul(doc.get("source_object_bucket", "")),
                    self._strip_nul(doc.get("source_object_key", "")),
                    self._strip_nul(doc.get("source_object_sha256", "")),
                    max(0, int(doc.get("source_object_size_bytes") or 0)),
                    self._strip_nul(doc.get("source_content_type", "")),
                    self._strip_nul(doc.get("extracted_object_key", "")),
                    self._strip_nul(doc.get("extracted_text_sha256", "")),
                    max(0, int(doc.get("extracted_object_size_bytes") or 0)),
                ),
            )
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, to_kb_id, doc_id))
            cur.execute("DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, to_kb_id, doc_id))
            for parent in parents:
                cur.execute(
                    """INSERT INTO chunk_parents
                    (tenant_id,kb_id,doc_id,parent_id,parent_index,text,section_path,page_start,page_end,
                     metadata,index_revision,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,NOW(),NOW())""",
                    (
                        tenant_id, to_kb_id, doc_id, parent["parent_id"], parent["parent_index"],
                        self._strip_nul(parent.get("text", "")), list(parent.get("section_path") or []),
                        int(parent.get("page_start") or 0), int(parent.get("page_end") or 0),
                        json.dumps(parent.get("metadata") or {}, ensure_ascii=False),
                        str(parent.get("index_revision") or self.rag_index_revision),
                    ),
                )
            for ch in chunks:
                lexical_tf = lexical_term_frequency(self._strip_nul(ch.get("text", "")))
                cur.execute(
                    """INSERT INTO chunks (
                        tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,
                        lexical_terms,lexical_tf,lexical_token_count,index_revision,parent_id,section_path,
                        block_type,page_start,page_end,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::text[],%s::jsonb,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())""",
                    (
                        tenant_id,
                        to_kb_id,
                        doc_id,
                        ch["chunk_id"],
                        int(ch.get("chunk_index") or 0),
                        self._strip_nul(ch.get("text", "")),
                        bool(ch.get("frozen", False)),
                        json.dumps(ch.get("embedding_json") or [], ensure_ascii=False),
                        json.dumps(ch.get("metadata") or {}, ensure_ascii=False),
                        list(lexical_tf),
                        json.dumps(lexical_tf, ensure_ascii=False),
                        sum(lexical_tf.values()),
                        self.rag_index_revision,
                        str(ch.get("parent_id") or ""),
                        list(ch.get("section_path") or []),
                        str(ch.get("block_type") or "paragraph"),
                        int(ch.get("page_start") or 0),
                        int(ch.get("page_end") or 0),
                    ),
                )
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, to_kb_id, doc_id))
            for dr in drafts:
                cur.execute(
                    """INSERT INTO chunk_drafts (tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (tenant_id,kb_id,doc_id,chunk_id) DO UPDATE SET
                        action=EXCLUDED.action,text=EXCLUDED.text,updated_at=NOW()""",
                    (
                        tenant_id,
                        to_kb_id,
                        doc_id,
                        dr.get("chunk_id"),
                        dr.get("action"),
                        self._strip_nul(dr.get("text", "")),
                    ),
                )
            cur.execute("DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            move_payload = {
                "source_kb_id": from_kb_id,
                "target_kb_id": to_kb_id,
                "original_status": original_status,
            }
            cur.execute(
                """INSERT INTO cross_store_repairs
                (store_name,operation,tenant_id,kb_id,doc_id,status,attempts,last_error,payload_json,created_at,updated_at)
                VALUES ('QDRANT','MOVE_DOCUMENT_POINTS',%s,%s,%s,'PENDING',0,'',%s::jsonb,NOW(),NOW())
                RETURNING repair_id""",
                (tenant_id, from_kb_id, doc_id, json.dumps(move_payload, ensure_ascii=False)),
            )
            repair_id = int(cur.fetchone()["repair_id"])
        self.recompute_kb_stats(tenant_id, from_kb_id)
        self.recompute_kb_stats(tenant_id, to_kb_id)
        if not self.repair_cross_store_operation(repair_id, _collection_lock_held=True):
            raise RuntimeError(f"document move repair pending: repair_id={repair_id}")
        return True

    def get_document(self, tenant_id: str, kb_id: str, doc_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,summary_overview,summary_text,summary_embedding_json,summary_dirty,text_size_bytes,word_count,
                tags_json,frozen,
                status,chunk_count,embedded_count,embedding_dirty,
                source_object_bucket,source_object_key,source_object_sha256,source_object_size_bytes,
                source_content_type,extracted_object_key,extracted_text_sha256,extracted_object_size_bytes,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at
                FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (tenant_id, kb_id, doc_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def set_document_object_metadata(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        *,
        source_bucket: str,
        source_key: str,
        source_sha256: str,
        source_size_bytes: int,
        source_content_type: str,
        extracted_key: str,
        extracted_sha256: str,
        extracted_size_bytes: int = 0,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents SET
                source_object_bucket=%s,source_object_key=%s,source_object_sha256=%s,
                source_object_size_bytes=%s,source_content_type=%s,
                extracted_object_key=%s,extracted_text_sha256=%s,
                extracted_object_size_bytes=%s,updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (
                    self._strip_nul(source_bucket),
                    self._strip_nul(source_key),
                    self._strip_nul(source_sha256),
                    max(0, int(source_size_bytes)),
                    self._strip_nul(source_content_type),
                    self._strip_nul(extracted_key),
                    self._strip_nul(extracted_sha256),
                    max(0, int(extracted_size_bytes)),
                    tenant_id,
                    kb_id,
                    doc_id,
                ),
            )
            if cur.rowcount == 0:
                raise RuntimeError("document disappeared before object metadata could be committed")

    def reserve_storage_quota(
        self,
        *,
        tenant_id: str,
        requested_bytes: int,
        quota_bytes: int,
        ttl_seconds: int = 300,
        reservation_id: str | None = None,
        kb_id: str = "",
        doc_id: str = "",
    ) -> dict:
        """Atomically reserve logical object capacity for one tenant.

        A tenant-scoped transaction advisory lock serializes the usage check and
        insert, so concurrent uploads cannot both pass against the same quota.
        A disabled quota (zero) intentionally creates no reservation row.
        """
        self._ensure_indexes()
        tenant = self._strip_nul(tenant_id).strip()
        requested = int(requested_bytes)
        quota = max(0, int(quota_bytes))
        ttl = max(30, min(int(ttl_seconds), 3600))
        if not tenant:
            raise ValueError("tenant_id is required for storage quota reservation")
        if requested <= 0:
            raise ValueError("requested_bytes must be positive")
        if quota == 0:
            return {
                "allowed": True,
                "reservation_id": "",
                "used_bytes": 0,
                "reserved_bytes": 0,
                "requested_bytes": requested,
                "projected_bytes": 0,
                "quota_bytes": 0,
                "existing_bytes": 0,
                "capacity_delta_bytes": requested,
            }

        reservation = self._strip_nul(reservation_id or str(uuid.uuid4())).strip()
        if not reservation:
            raise ValueError("reservation_id is required")
        with psycopg.connect(self.dsn, autocommit=False, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"storage-quota:{tenant}",))
            cur.execute(
                "DELETE FROM storage_quota_reservations WHERE tenant_id=%s AND expires_at <= NOW()",
                (tenant,),
            )
            cur.execute(
                """SELECT COALESCE(SUM(source_object_size_bytes + extracted_object_size_bytes), 0)::bigint AS used_bytes
                FROM documents WHERE tenant_id=%s""",
                (tenant,),
            )
            used = int((cur.fetchone() or {}).get("used_bytes") or 0)
            existing = 0
            safe_kb = self._strip_nul(kb_id).strip()
            safe_doc = self._strip_nul(doc_id).strip()
            if safe_kb and safe_doc:
                cur.execute(
                    """SELECT (source_object_size_bytes + extracted_object_size_bytes)::bigint AS existing_bytes
                    FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                    (tenant, safe_kb, safe_doc),
                )
                existing = int((cur.fetchone() or {}).get("existing_bytes") or 0)
            capacity_delta = max(0, requested - existing)
            cur.execute(
                """SELECT COALESCE(SUM(reserved_bytes), 0)::bigint AS reserved_bytes
                FROM storage_quota_reservations WHERE tenant_id=%s AND expires_at > NOW()""",
                (tenant,),
            )
            reserved = int((cur.fetchone() or {}).get("reserved_bytes") or 0)
            projected = used + reserved + capacity_delta
            allowed = projected <= quota
            if allowed and capacity_delta > 0:
                cur.execute(
                    """INSERT INTO storage_quota_reservations
                    (reservation_id,tenant_id,reserved_bytes,expires_at,created_at)
                    VALUES (%s,%s,%s,NOW() + (%s * INTERVAL '1 second'),NOW())""",
                    (reservation, tenant, capacity_delta, ttl),
                )
        return {
            "allowed": allowed,
            "reservation_id": reservation if allowed and capacity_delta > 0 else "",
            "used_bytes": used,
            "reserved_bytes": reserved,
            "requested_bytes": requested,
            "projected_bytes": projected,
            "quota_bytes": quota,
            "existing_bytes": existing,
            "capacity_delta_bytes": capacity_delta,
        }

    def release_storage_quota(self, reservation_id: str) -> bool:
        self._ensure_indexes()
        reservation = self._strip_nul(reservation_id).strip()
        if not reservation:
            return False
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM storage_quota_reservations WHERE reservation_id=%s", (reservation,))
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def storage_capacity_stats(self, tenant_limit: int = 100) -> dict:
        """Return authoritative logical object bytes and active reservations."""
        self._ensure_indexes()
        cap = max(1, min(int(tenant_limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,
                    COUNT(*)::bigint AS document_count,
                    COALESCE(SUM(source_object_size_bytes),0)::bigint AS source_bytes,
                    COALESCE(SUM(extracted_object_size_bytes),0)::bigint AS extracted_bytes
                FROM documents
                GROUP BY tenant_id"""
            )
            documents = {str(row["tenant_id"]): dict(row) for row in cur.fetchall()}
            cur.execute(
                """SELECT tenant_id,COUNT(*)::bigint AS reservation_count,
                    COALESCE(SUM(reserved_bytes),0)::bigint AS reserved_bytes,
                    MIN(expires_at) AS next_expiry_at
                FROM storage_quota_reservations
                WHERE expires_at > NOW()
                GROUP BY tenant_id"""
            )
            reservations = {str(row["tenant_id"]): dict(row) for row in cur.fetchall()}

        tenants: list[dict] = []
        for tenant in set(documents) | set(reservations):
            doc = documents.get(tenant) or {}
            held = reservations.get(tenant) or {}
            source = int(doc.get("source_bytes") or 0)
            extracted = int(doc.get("extracted_bytes") or 0)
            reserved = int(held.get("reserved_bytes") or 0)
            tenants.append(
                {
                    "tenant_id": tenant,
                    "document_count": int(doc.get("document_count") or 0),
                    "source_bytes": source,
                    "extracted_bytes": extracted,
                    "used_bytes": source + extracted,
                    "reservation_count": int(held.get("reservation_count") or 0),
                    "reserved_bytes": reserved,
                    "projected_bytes": source + extracted + reserved,
                    "next_expiry_at": held.get("next_expiry_at"),
                }
            )
        tenants.sort(key=lambda row: (-int(row["projected_bytes"]), str(row["tenant_id"])))
        visible = tenants[:cap]
        return {
            "tenant_count": len(tenants),
            "document_count": sum(int(row["document_count"]) for row in tenants),
            "source_bytes": sum(int(row["source_bytes"]) for row in tenants),
            "extracted_bytes": sum(int(row["extracted_bytes"]) for row in tenants),
            "used_bytes": sum(int(row["used_bytes"]) for row in tenants),
            "reservation_count": sum(int(row["reservation_count"]) for row in tenants),
            "reserved_bytes": sum(int(row["reserved_bytes"]) for row in tenants),
            "projected_bytes": sum(int(row["projected_bytes"]) for row in tenants),
            "items": visible,
            "truncated": len(tenants) > len(visible),
        }

    def upsert_alertmanager_alert(
        self,
        *,
        fingerprint: str,
        alert_name: str,
        component: str,
        severity: str,
        status: str,
        labels: dict,
        annotations: dict,
        starts_at: datetime | None,
        ends_at: datetime | None,
    ) -> None:
        self._ensure_indexes()
        safe_fingerprint = self._strip_nul(fingerprint).strip()[:128]
        safe_name = self._strip_nul(alert_name).strip()[:200]
        safe_component = self._strip_nul(component).strip()[:100]
        safe_severity = self._strip_nul(severity).strip().lower()[:32] or "warning"
        safe_status = self._strip_nul(status).strip().upper()
        if not safe_fingerprint or not safe_name:
            raise ValueError("alert fingerprint and name are required")
        if safe_status not in {"FIRING", "RESOLVED"}:
            raise ValueError("alert status must be FIRING or RESOLVED")
        safe_labels = {
            self._strip_nul(str(key))[:100]: self._strip_nul(str(value))[:500]
            for key, value in dict(labels or {}).items()
            if str(key).strip()
        }
        safe_annotations = {
            self._strip_nul(str(key))[:100]: self._strip_nul(str(value))[:2000]
            for key, value in dict(annotations or {}).items()
            if str(key).strip()
        }
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO alertmanager_alerts (
                    fingerprint,alert_name,component,severity,status,labels_json,annotations_json,
                    starts_at,ends_at,first_received_at,last_received_at,resolved_at
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,NOW(),NOW(),
                    CASE WHEN %s='RESOLVED' THEN NOW() ELSE NULL END)
                ON CONFLICT (fingerprint) DO UPDATE SET
                    alert_name=EXCLUDED.alert_name,
                    component=EXCLUDED.component,
                    severity=EXCLUDED.severity,
                    status=EXCLUDED.status,
                    labels_json=EXCLUDED.labels_json,
                    annotations_json=EXCLUDED.annotations_json,
                    starts_at=COALESCE(EXCLUDED.starts_at,alertmanager_alerts.starts_at),
                    ends_at=EXCLUDED.ends_at,
                    last_received_at=NOW(),
                    resolved_at=CASE WHEN EXCLUDED.status='RESOLVED' THEN NOW() ELSE NULL END""",
                (
                    safe_fingerprint,
                    safe_name,
                    safe_component,
                    safe_severity,
                    safe_status,
                    json.dumps(safe_labels, ensure_ascii=False),
                    json.dumps(safe_annotations, ensure_ascii=False),
                    starts_at,
                    ends_at,
                    safe_status,
                ),
            )

    def list_alertmanager_alerts(self, *, status: str = "FIRING", limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        safe_status = self._strip_nul(status).strip().upper()
        if safe_status not in {"", "FIRING", "RESOLVED"}:
            raise ValueError("invalid Alertmanager alert status")
        cap = max(1, min(int(limit), 500))
        where = "WHERE status=%s" if safe_status else ""
        params: tuple = (safe_status, cap) if safe_status else (cap,)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT fingerprint,alert_name,component,severity,status,labels_json,annotations_json,
                starts_at,ends_at,first_received_at,last_received_at,resolved_at
                FROM alertmanager_alerts {where}
                ORDER BY last_received_at DESC LIMIT %s""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def cleanup_resolved_alertmanager_alerts(self, retention_days: int = 90) -> int:
        self._ensure_indexes()
        keep_days = max(1, min(int(retention_days), 3650))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """DELETE FROM alertmanager_alerts
                WHERE status='RESOLVED' AND resolved_at < (NOW() - (%s * INTERVAL '1 day'))""",
                (keep_days,),
            )
            return int(cur.rowcount or 0)

    def set_document_status(self, tenant_id: str, kb_id: str, doc_id: str, status: str) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (status, tenant_id, kb_id, doc_id),
            )

    def set_document_frozen(self, tenant_id: str, kb_id: str, doc_id: str, frozen: bool) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET frozen=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (bool(frozen), tenant_id, kb_id, doc_id),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def set_chunk_frozen(self, tenant_id: str, kb_id: str, doc_id: str, chunk_id: str, frozen: bool) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE chunks
                SET frozen=%s, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_id=%s""",
                (bool(frozen), tenant_id, kb_id, doc_id, chunk_id),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def set_document_summary(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        overview: str,
        summary: str,
        embedding: list[float] | None = None,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET summary_overview=%s, summary_text=%s, summary_embedding_json=%s::jsonb, summary_dirty=FALSE, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (
                    self._strip_nul(overview),
                    self._strip_nul(summary),
                    json.dumps(embedding or [], ensure_ascii=False),
                    tenant_id,
                    kb_id,
                    doc_id,
                ),
            )
        self.recompute_kb_stats(tenant_id, kb_id)

    def list_document_summaries(self, tenant_id: str, kb_id: str, limit: int = 200) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT doc_id,filename,summary_overview,summary_text,updated_at
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s
                ORDER BY updated_at DESC
                LIMIT %s""",
                (tenant_id, kb_id, cap),
            )
            return [dict(r) for r in cur.fetchall()]

    def should_auto_first_kb_summary(self, tenant_id: str, kb_id: str) -> bool:
        """Auto summarize KB only once, and only after all docs have summaries."""
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT summary FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            kb = cur.fetchone()
            if not kb:
                return False
            kb_summary = str(kb.get("summary") or "").strip()
            if kb_summary:
                return False
            cur.execute(
                """SELECT
                    COUNT(*)::int AS total_docs,
                    COUNT(*) FILTER (WHERE COALESCE(BTRIM(summary_text), '') <> '')::int AS summarized_docs
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            row = cur.fetchone() or {"total_docs": 0, "summarized_docs": 0}
            total_docs = int(row.get("total_docs") or 0)
            summarized_docs = int(row.get("summarized_docs") or 0)
            return total_docs > 0 and summarized_docs == total_docs

    def set_kb_summary(
        self,
        tenant_id: str,
        kb_id: str,
        summary: str,
        embedding: list[float] | None = None,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE knowledge_bases
                SET summary=%s, summary_embedding_json=%s::jsonb, summary_dirty=FALSE, summary_updated_at=NOW(), updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s""",
                (
                    self._strip_nul(summary),
                    json.dumps(embedding or [], ensure_ascii=False),
                    tenant_id,
                    kb_id,
                ),
            )

    def mark_document_failed(self, tenant_id: str, kb_id: str, doc_id: str) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status='FAILED', embedding_dirty=TRUE, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (tenant_id, kb_id, doc_id),
            )

    def _update_document_counters(self, tenant_id: str, kb_id: str, doc_id: str, chunk_count: int, embedded_count: int, embedding_dirty: bool) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET chunk_count=%s, embedded_count=%s, embedding_dirty=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (int(chunk_count), int(embedded_count), bool(embedding_dirty), tenant_id, kb_id, doc_id),
            )

    def set_document_chunking_preview(self, tenant_id: str, kb_id: str, doc_id: str, chunk_count: int) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET status='CHUNKING', chunk_count=%s, embedded_count=0, embedding_dirty=TRUE, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (int(chunk_count), tenant_id, kb_id, doc_id),
            )

    def replace_chunks_preview(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunk_texts: list[str],
        filename: str,
    ) -> None:
        """Persist chunk texts immediately, without vectors.

        This allows UI to show freshly generated chunks during CHUNKING while
        EMBEDDING/INDEXING continues in background.
        """
        self._ensure_indexes()
        safe_filename = self._strip_nul(filename or "unknown")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            for idx, text in enumerate(chunk_texts):
                safe_text = self._strip_nul(text)
                lexical_tf = lexical_term_frequency(safe_text)
                raw = f"{tenant_id}:{kb_id}:{doc_id}:{idx}:{safe_text}"
                chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
                cur.execute(
                    """INSERT INTO chunks (
                        tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,
                        lexical_terms,lexical_tf,lexical_token_count,index_revision,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s::jsonb,%s::text[],%s::jsonb,%s,%s,NOW(),NOW())""",
                    (
                        tenant_id,
                        kb_id,
                        doc_id,
                        chunk_id,
                        idx,
                        safe_text,
                        "[]",
                        json.dumps({"filename": safe_filename}, ensure_ascii=False),
                        list(lexical_tf),
                        json.dumps(lexical_tf, ensure_ascii=False),
                        sum(lexical_tf.values()),
                        self.rag_index_revision,
                    ),
                )
        # Do not mutate Qdrant while a new generation is only a preview. The
        # document status is the visibility barrier; publish_document_embeddings
        # replaces the complete document generation after every vector exists.
        self._update_document_counters(tenant_id, kb_id, doc_id, len(chunk_texts), 0, True)
        self.recompute_kb_stats(tenant_id, kb_id)

    def replace_chunk_structure(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunks: list[dict],
        filename: str,
    ) -> None:
        """Persist parent contexts and structural metadata for preview chunks."""
        self._ensure_indexes()
        safe_filename = self._strip_nul(filename or "unknown")
        parents: dict[str, dict] = {}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (tenant_id, kb_id, doc_id),
            )
            for chunk_index, raw in enumerate(chunks):
                parent_id = str(raw.get("parent_id") or "")
                section_path = [self._strip_nul(str(item)) for item in (raw.get("section_path") or [])]
                block_type = self._strip_nul(str(raw.get("block_type") or "paragraph"))
                page_start = max(0, int(raw.get("page_start") or 0))
                page_end = max(page_start, int(raw.get("page_end") or page_start))
                metadata = {
                    "filename": safe_filename,
                    "parent_id": parent_id,
                    "parent_index": max(0, int(raw.get("parent_index") or 0)),
                    "section_path": section_path,
                    "block_type": block_type,
                    "page_start": page_start,
                    "page_end": page_end,
                }
                cur.execute(
                    """UPDATE chunks SET parent_id=%s,section_path=%s,block_type=%s,
                        page_start=%s,page_end=%s,metadata=%s::jsonb,updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_index=%s""",
                    (
                        parent_id, section_path, block_type, page_start, page_end,
                        json.dumps(metadata, ensure_ascii=False), tenant_id, kb_id, doc_id, chunk_index,
                    ),
                )
                if parent_id and parent_id not in parents:
                    parents[parent_id] = {
                        **metadata,
                        "text": self._strip_nul(str(raw.get("parent_text") or "")),
                    }
            for parent_id, parent in parents.items():
                cur.execute(
                    """INSERT INTO chunk_parents
                    (tenant_id,kb_id,doc_id,parent_id,parent_index,text,section_path,page_start,page_end,
                     metadata,index_revision,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,NOW(),NOW())""",
                    (
                        tenant_id, kb_id, doc_id, parent_id, parent["parent_index"], parent["text"],
                        parent["section_path"], parent["page_start"], parent["page_end"],
                        json.dumps({"filename": safe_filename}, ensure_ascii=False), self.rag_index_revision,
                    ),
                )

    def list_document_chunks_for_embedding(self, tenant_id: str, kb_id: str, doc_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,embedding_json,metadata,
                    parent_id,section_path,block_type,page_start,page_end
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC""",
                (tenant_id, kb_id, doc_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def persist_chunk_embedding(self, chunk: ChunkRecord) -> None:
        """Persist an embedding in PostgreSQL without publishing a partial index."""
        self._ensure_indexes()
        safe_text = self._strip_nul(chunk.text)
        metadata = dict(chunk.metadata or {})
        safe_filename = self._strip_nul(metadata.get("filename", "unknown"))
        metadata["filename"] = safe_filename
        vector = [float(x) for x in (chunk.embedding or [])]
        if not vector:
            raise ValueError("embedding vector is empty")
        lexical_tf = lexical_term_frequency(safe_text)
        lexical_terms = sorted(lexical_tf)
        lexical_token_count = sum(int(value) for value in lexical_tf.values())
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE chunks
                SET text=%s, embedding_json=%s::jsonb, metadata=%s::jsonb,
                    lexical_terms=%s,lexical_tf=%s::jsonb,lexical_token_count=%s,
                    index_revision=%s,updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_id=%s""",
                (
                    safe_text,
                    json.dumps(vector, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    lexical_terms,
                    json.dumps(lexical_tf, ensure_ascii=False),
                    lexical_token_count,
                    self.rag_index_revision,
                    chunk.tenant_id,
                    chunk.kb_id,
                    chunk.doc_id,
                    chunk.chunk_id,
                ),
            )
        self.refresh_document_embedding_counters(chunk.tenant_id, chunk.kb_id, chunk.doc_id)

    def publish_document_embeddings(self, tenant_id: str, kb_id: str, doc_id: str) -> dict:
        """Replace one document's Qdrant generation from its PostgreSQL source of truth.

        The caller must keep the document non-INDEXED until this method succeeds.
        Repeating the operation is safe: existing points for the document are
        deleted and the complete current generation is then upserted.
        """
        self._ensure_indexes()
        rows = self.list_document_chunks_for_embedding(tenant_id, kb_id, doc_id)
        chunks: list[ChunkRecord] = []
        vector_size: int | None = None
        for row in rows:
            vector = [float(x) for x in (row.get("embedding_json") or [])]
            if not vector:
                raise RuntimeError(f"document embedding incomplete: chunk {row.get('chunk_id') or ''}")
            if vector_size is None:
                vector_size = len(vector)
            elif len(vector) != vector_size:
                raise RuntimeError("document embedding dimensions are inconsistent")
            chunks.append(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    chunk_id=str(row.get("chunk_id") or ""),
                    chunk_index=int(row.get("chunk_index") or 0),
                    text=self._strip_nul(str(row.get("text") or "")),
                    embedding=vector,
                    metadata=row.get("metadata") or {},
                )
            )

        collection_name = self._collection_name(tenant_id, kb_id)
        if chunks:
            self._ensure_collection(collection_name, int(vector_size or 0))
        if self.qdrant.collection_exists(collection_name=collection_name):
            self.qdrant.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                            qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
            )
        if chunks:
            # Dense vectors are JSON encoded by the HTTP client.  Even a modest
            # point count can therefore create a large request body and make a
            # constrained Qdrant instance stop accepting new requests.  Keep
            # the safe default deliberately small; operators can raise it after
            # measuring their vector dimensions and Qdrant limits.
            batch_size = max(8, min(int(os.getenv("QDRANT_UPSERT_BATCH_SIZE", "16")), 512))
            points = [
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                    vector=chunk.embedding,
                    payload={
                        "tenant_id": chunk.tenant_id,
                        "kb_id": chunk.kb_id,
                        "doc_id": chunk.doc_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "text": self._strip_nul(chunk.text),
                        "source": self._strip_nul((chunk.metadata or {}).get("filename", "unknown")),
                        "parent_id": str((chunk.metadata or {}).get("parent_id") or ""),
                        "section_path": list((chunk.metadata or {}).get("section_path") or []),
                        "block_type": str((chunk.metadata or {}).get("block_type") or "paragraph"),
                        "page_start": int((chunk.metadata or {}).get("page_start") or 0),
                        "page_end": int((chunk.metadata or {}).get("page_end") or 0),
                        "index_revision": self.rag_index_revision,
                    },
                )
                for chunk in chunks
            ]
            for offset in range(0, len(points), batch_size):
                self.qdrant.upsert(
                    collection_name=collection_name,
                    points=points[offset:offset + batch_size],
                )
        counters = self.refresh_document_embedding_counters(tenant_id, kb_id, doc_id)
        self.recompute_kb_stats(tenant_id, kb_id)
        return counters

    def refresh_document_embedding_counters(self, tenant_id: str, kb_id: str, doc_id: str) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                    COUNT(*)::int AS chunk_count,
                    COUNT(*) FILTER (WHERE jsonb_typeof(embedding_json)='array' AND jsonb_array_length(embedding_json)>0)::int AS embedded_count
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (tenant_id, kb_id, doc_id),
            )
            row = cur.fetchone() or {}
        chunk_count = int(row.get("chunk_count") or 0)
        embedded_count = int(row.get("embedded_count") or 0)
        self._update_document_counters(tenant_id, kb_id, doc_id, chunk_count, embedded_count, embedded_count < chunk_count)
        return {"chunk_count": chunk_count, "embedded_count": embedded_count}

    def _ensure_collection(self, collection_name: str, vector_size: int) -> None:
        if self.qdrant.collection_exists(collection_name=collection_name):
            current_size = self._get_collection_vector_size(collection_name)
            if current_size is None or current_size == int(vector_size):
                return
            # A collection is shared by all documents in the KB. Recreating it
            # for one document would silently erase every other indexed
            # document, so dimension migration must be an explicit KB rebuild.
            raise RuntimeError(
                f"qdrant collection dimension mismatch: current={current_size}, requested={int(vector_size)}; "
                "run a KB-wide reindex"
            )
        try:
            self.qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
            )
        except ApiException:
            if not self.qdrant.collection_exists(collection_name=collection_name):
                raise

    def _get_collection_vector_size(self, collection_name: str) -> int | None:
        try:
            info = self.qdrant.get_collection(collection_name=collection_name)
        except Exception:
            return None

        # qdrant-client object path: info.config.params.vectors
        vectors_cfg = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
        if vectors_cfg is None and isinstance(info, dict):
            vectors_cfg = (
                (((info.get("config") or {}).get("params") or {}).get("vectors"))
                if isinstance(info.get("config"), dict)
                else None
            )

        if vectors_cfg is None:
            return None
        if hasattr(vectors_cfg, "size"):
            try:
                return int(getattr(vectors_cfg, "size"))
            except Exception:
                return None
        if isinstance(vectors_cfg, dict):
            if "size" in vectors_cfg:
                try:
                    return int(vectors_cfg.get("size"))
                except Exception:
                    return None
            for item in vectors_cfg.values():
                if hasattr(item, "size"):
                    try:
                        return int(getattr(item, "size"))
                    except Exception:
                        continue
                if isinstance(item, dict) and "size" in item:
                    try:
                        return int(item.get("size"))
                    except Exception:
                        continue
        return None

    def replace_chunks(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunks: list[ChunkRecord],
        *,
        _collection_lock_held: bool = False,
    ) -> None:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            for c in chunks:
                safe_text = self._strip_nul(c.text)
                safe_filename = self._strip_nul((c.metadata or {}).get("filename", "unknown"))
                lexical_tf = lexical_term_frequency(safe_text)
                cur.execute(
                    """INSERT INTO chunks (
                        tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,
                        lexical_terms,lexical_tf,lexical_token_count,index_revision,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s::jsonb,%s::text[],%s::jsonb,%s,%s,NOW(),NOW())""",
                    (
                        c.tenant_id,
                        c.kb_id,
                        c.doc_id,
                        c.chunk_id,
                        c.chunk_index,
                        safe_text,
                        json.dumps(c.embedding, ensure_ascii=False),
                        json.dumps({"filename": safe_filename}, ensure_ascii=False),
                        list(lexical_tf),
                        json.dumps(lexical_tf, ensure_ascii=False),
                        sum(lexical_tf.values()),
                        self.rag_index_revision,
                    ),
                )
        if chunks and _collection_lock_held:
            self._replace_qdrant_document_chunks(collection_name, tenant_id, kb_id, doc_id, chunks)
        elif chunks:
            with self._qdrant_collection_locks([collection_name]):
                self._replace_qdrant_document_chunks(collection_name, tenant_id, kb_id, doc_id, chunks)
        self._update_document_counters(tenant_id, kb_id, doc_id, len(chunks), len(chunks), False)
        self.recompute_kb_stats(tenant_id, kb_id)

    def _replace_qdrant_document_chunks(
        self,
        collection_name: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunks: list[ChunkRecord],
    ) -> None:
        self._ensure_collection(collection_name, len(chunks[0].embedding))
        self.qdrant.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                        qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                        qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                    ]
                )
            ),
        )
        self.qdrant.upsert(
            collection_name=collection_name,
            points=[
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                    vector=chunk.embedding,
                    payload={
                        "tenant_id": chunk.tenant_id,
                        "kb_id": chunk.kb_id,
                        "doc_id": chunk.doc_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "text": self._strip_nul(chunk.text),
                        "source": self._strip_nul((chunk.metadata or {}).get("filename", "unknown")),
                        "parent_id": str((chunk.metadata or {}).get("parent_id") or ""),
                        "section_path": list((chunk.metadata or {}).get("section_path") or []),
                        "block_type": str((chunk.metadata or {}).get("block_type") or "paragraph"),
                        "page_start": int((chunk.metadata or {}).get("page_start") or 0),
                        "page_end": int((chunk.metadata or {}).get("page_end") or 0),
                        "index_revision": self.rag_index_revision,
                    },
                )
                for chunk in chunks
            ],
        )

    def search_chunks(
        self,
        tenant_id: str,
        kb_id: str,
        query_vector: list[float],
        top_k: int,
        doc_ids: list[str] | None = None,
        index_revision: str | None = None,
    ) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT frozen FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            kb = cur.fetchone()
            if not kb:
                return []
            if bool(kb.get("frozen")):
                return []
        revision = str(index_revision or self.get_active_index_revision(tenant_id, kb_id))
        snapshot = self.has_index_snapshot(
            tenant_id, kb_id, revision, allow_pending=index_revision is not None
        )
        collection_name = (
            self._index_collection_name(tenant_id, kb_id, revision)
            if snapshot else self._collection_name(tenant_id, kb_id)
        )
        conditions = [
            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
        ]
        if snapshot:
            conditions.append(qmodels.FieldCondition(key="index_revision", match=qmodels.MatchValue(value=revision)))
        if doc_ids:
            conditions.append(qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=doc_ids)))
        requested_top_k = max(1, min(int(top_k), 50))
        # Fetch extra candidates because stale/frozen SQL rows are filtered below.
        # Returning exactly requested_top_k is only possible if enough valid chunks remain.
        candidate_limit = max(requested_top_k, min(requested_top_k * 3, 100))
        results = self.qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=qmodels.Filter(must=conditions),
            limit=candidate_limit,
            with_payload=True,
        )
        items = [
            {
                "doc_id": (hit.payload or {}).get("doc_id", ""),
                "chunk_id": (hit.payload or {}).get("chunk_id", ""),
                "chunk_index": (hit.payload or {}).get("chunk_index", 0),
                "text": (hit.payload or {}).get("text", ""),
                "score": float(hit.score),
                "scores": {"dense": float(hit.score)},
                "rank_method": "vector",
                "source": (hit.payload or {}).get("source", "unknown"),
                "parent_id": (hit.payload or {}).get("parent_id", ""),
                "section_path": list((hit.payload or {}).get("section_path") or []),
                "block_type": (hit.payload or {}).get("block_type", "paragraph"),
                "page_start": (hit.payload or {}).get("page_start", 0),
                "page_end": (hit.payload or {}).get("page_end", 0),
                "index_revision": (hit.payload or {}).get("index_revision", "legacy-v1"),
            }
            for hit in results
        ]
        if not items:
            return []

        hit_doc_ids = list({str(item.get("doc_id") or "") for item in items if str(item.get("doc_id") or "")})
        if not hit_doc_ids:
            return []

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT doc_id,filename,frozen,status
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s AND doc_id = ANY(%s)""",
                (tenant_id, kb_id, hit_doc_ids),
            )
            doc_rows = [dict(r) for r in cur.fetchall()]
            doc_frozen_map = {str(r.get("doc_id") or ""): bool(r.get("frozen")) for r in doc_rows}
            doc_status_map = {str(r.get("doc_id") or ""): str(r.get("status") or "") for r in doc_rows}
            doc_filename_map = {str(r.get("doc_id") or ""): self._strip_nul(str(r.get("filename") or "")) for r in doc_rows}

            if snapshot:
                cur.execute(
                    """SELECT doc_id,chunk_id,frozen,parent_id,section_path,block_type,page_start,page_end
                    FROM rag_index_chunks
                    WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s AND doc_id = ANY(%s)""",
                    (tenant_id, kb_id, revision, hit_doc_ids),
                )
            else:
                cur.execute(
                    """SELECT doc_id,chunk_id,frozen,parent_id,section_path,block_type,page_start,page_end
                    FROM chunks
                    WHERE tenant_id=%s AND kb_id=%s AND doc_id = ANY(%s)""",
                    (tenant_id, kb_id, hit_doc_ids),
                )
            chunk_rows = [dict(r) for r in cur.fetchall()]
            chunk_frozen_map = {
                (str(r.get("doc_id") or ""), str(r.get("chunk_id") or "")): bool(r.get("frozen"))
                for r in chunk_rows
            }
            chunk_structure_map = {
                (str(r.get("doc_id") or ""), str(r.get("chunk_id") or "")): r
                for r in chunk_rows
            }

        filtered: list[dict] = []
        for item in items:
            doc_id = str(item.get("doc_id") or "")
            chunk_id = str(item.get("chunk_id") or "")
            if not doc_id or not chunk_id:
                continue
            # Drop stale vector hits that no longer exist in SQL.
            if doc_id not in doc_frozen_map:
                continue
            # PostgreSQL status is the publish/visibility barrier. Qdrant may
            # temporarily contain stale or partially replaced points after a
            # remote failure, but only a fully published document is visible.
            if doc_status_map.get(doc_id) != "INDEXED":
                continue
            if (doc_id, chunk_id) not in chunk_frozen_map:
                continue
            if doc_frozen_map.get(doc_id, False):
                continue
            if chunk_frozen_map.get((doc_id, chunk_id), False):
                continue
            filename = doc_filename_map.get(doc_id) or self._strip_nul(str(item.get("source") or "unknown"))
            structure = chunk_structure_map.get((doc_id, chunk_id)) or {}
            item["filename"] = filename
            item["document_name"] = filename
            item["source"] = filename
            item["parent_id"] = str(structure.get("parent_id") or item.get("parent_id") or "")
            item["section_path"] = list(structure.get("section_path") or item.get("section_path") or [])
            item["block_type"] = str(structure.get("block_type") or item.get("block_type") or "paragraph")
            item["page_start"] = int(structure.get("page_start") or item.get("page_start") or 0)
            item["page_end"] = int(structure.get("page_end") or item.get("page_end") or 0)
            item["index_revision"] = revision if snapshot else str(item.get("index_revision") or revision)
            filtered.append(item)
            if len(filtered) >= requested_top_k:
                break
        return filtered

    def search_lexical_chunks(
        self,
        tenant_id: str,
        kb_id: str,
        query: str,
        top_k: int,
        doc_ids: list[str] | None = None,
        index_revision: str | None = None,
    ) -> list[dict]:
        self._ensure_indexes()
        query_tf = lexical_term_frequency(query, max_terms=64)
        query_terms = sorted(query_tf)
        if not query_terms:
            return []
        requested = max(1, min(int(top_k or 1), 200))
        candidate_limit = min(max(requested * 5, 50), 500)
        revision = str(index_revision or self.get_active_index_revision(tenant_id, kb_id))
        snapshot = self.has_index_snapshot(
            tenant_id, kb_id, revision, allow_pending=index_revision is not None
        )
        chunk_table = "rag_index_chunks" if snapshot else "chunks"
        recency_column = "c.created_at" if snapshot else "c.updated_at"
        revision_filter = " AND c.index_revision=%s" if snapshot else ""
        scope_params: list[object] = [tenant_id, kb_id]
        if snapshot:
            scope_params.append(revision)
        params: list[object] = list(scope_params) + [query_terms]
        doc_filter = ""
        if doc_ids:
            doc_filter = " AND c.doc_id = ANY(%s)"
            params.append([str(item) for item in doc_ids if str(item)])
        params.extend([query_terms, candidate_limit])
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT c.doc_id,c.chunk_id,c.chunk_index,c.text,c.lexical_tf,
                    c.lexical_token_count,c.index_revision,c.parent_id,c.section_path,c.block_type,
                    c.page_start,c.page_end,d.filename
                FROM {chunk_table} c
                JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                WHERE c.tenant_id=%s AND c.kb_id=%s
                  {revision_filter}
                  AND c.lexical_terms && %s::text[]
                  AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                  {doc_filter}
                ORDER BY (
                    SELECT count(*) FROM unnest(c.lexical_terms) AS term
                    WHERE term = ANY(%s::text[])
                ) DESC,{recency_column} DESC
                LIMIT %s""",
                tuple(params),
            )
            candidates = [dict(row) for row in cur.fetchall()]
            if not candidates:
                return []
            aggregate_params: list[object] = list(scope_params)
            aggregate_filter = ""
            if doc_ids:
                aggregate_filter = " AND c.doc_id = ANY(%s)"
                aggregate_params.append([str(item) for item in doc_ids if str(item)])
            cur.execute(
                f"""SELECT count(*)::int AS document_count,
                    COALESCE(avg(NULLIF(c.lexical_token_count,0)),1)::float AS average_length
                FROM {chunk_table} c
                JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                WHERE c.tenant_id=%s AND c.kb_id=%s
                  {revision_filter}
                  AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                  {aggregate_filter}""",
                tuple(aggregate_params),
            )
            corpus = dict(cur.fetchone() or {})
            frequency_params = list(aggregate_params) + [query_terms, query_terms]
            cur.execute(
                f"""SELECT term,count(*)::int AS frequency
                FROM {chunk_table} c
                JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                CROSS JOIN LATERAL unnest(c.lexical_terms) AS term
                WHERE c.tenant_id=%s AND c.kb_id=%s
                  {revision_filter}
                  AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                  {aggregate_filter}
                  AND c.lexical_terms && %s::text[]
                  AND term = ANY(%s::text[])
                GROUP BY term""",
                tuple(frequency_params),
            )
            document_frequency = {str(row["term"]): int(row["frequency"]) for row in cur.fetchall()}
        document_count = int(corpus.get("document_count") or 0)
        average_length = float(corpus.get("average_length") or 1.0)
        items: list[dict] = []
        for row in candidates:
            lexical_tf = dict(row.get("lexical_tf") or {})
            score = bm25_score(
                query_terms,
                lexical_tf,
                int(row.get("lexical_token_count") or 0),
                average_length,
                document_frequency,
                document_count,
            )
            if score <= 0:
                continue
            filename = self._strip_nul(str(row.get("filename") or "unknown"))
            items.append(
                {
                    "doc_id": str(row.get("doc_id") or ""),
                    "chunk_id": str(row.get("chunk_id") or ""),
                    "chunk_index": int(row.get("chunk_index") or 0),
                    "text": self._strip_nul(str(row.get("text") or "")),
                    "score": float(score),
                    "scores": {"lexical": float(score)},
                    "rank_method": "keyword",
                    "source": filename,
                    "filename": filename,
                    "document_name": filename,
                    "parent_id": str(row.get("parent_id") or ""),
                    "section_path": list(row.get("section_path") or []),
                    "block_type": str(row.get("block_type") or "paragraph"),
                    "page_start": int(row.get("page_start") or 0),
                    "page_end": int(row.get("page_end") or 0),
                    "index_revision": revision if snapshot else str(row.get("index_revision") or revision),
                }
            )
        items.sort(key=lambda item: (-float(item["score"]), str(item["chunk_id"])))
        return items[:requested]

    def expand_chunk_context(
        self,
        tenant_id: str,
        kb_id: str,
        items: list[dict],
        *,
        window: int = 1,
        index_revision: str | None = None,
    ) -> list[dict]:
        """Attach bounded neighboring chunks while preserving the ranked hit identity."""
        self._ensure_indexes()
        radius = max(0, min(int(window or 0), 2))
        if radius <= 0 or not items:
            return items
        revision = str(index_revision or self.get_active_index_revision(tenant_id, kb_id))
        snapshot = self.has_index_snapshot(
            tenant_id, kb_id, revision, allow_pending=index_revision is not None
        )
        expanded: list[dict] = []
        with self._conn() as conn, conn.cursor() as cur:
            for raw in items:
                item = dict(raw)
                doc_id = str(item.get("doc_id") or "")
                chunk_index = int(item.get("chunk_index") or 0)
                if not doc_id:
                    expanded.append(item)
                    continue
                parent_id = str(item.get("parent_id") or "")
                if parent_id:
                    if snapshot:
                        cur.execute(
                            """SELECT text,section_path,page_start,page_end
                            FROM rag_index_parents
                            WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s
                              AND doc_id=%s AND parent_id=%s""",
                            (tenant_id, kb_id, revision, doc_id, parent_id),
                        )
                    else:
                        cur.execute(
                            """SELECT text,section_path,page_start,page_end
                            FROM chunk_parents
                            WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND parent_id=%s""",
                            (tenant_id, kb_id, doc_id, parent_id),
                        )
                    parent = dict(cur.fetchone() or {})
                    parent_text = self._strip_nul(str(parent.get("text") or ""))
                    if parent_text:
                        item["matched_text"] = str(item.get("text") or "")
                        item["text"] = parent_text
                        item["parent_context"] = True
                        item["context_strategy"] = "parent"
                        item["section_path"] = list(parent.get("section_path") or item.get("section_path") or [])
                        item["page_start"] = int(parent.get("page_start") or item.get("page_start") or 0)
                        item["page_end"] = int(parent.get("page_end") or item.get("page_end") or 0)
                        item["context_window"] = radius
                        expanded.append(item)
                        continue
                if snapshot:
                    cur.execute(
                        """SELECT c.chunk_id,c.chunk_index,c.text
                        FROM rag_index_chunks c
                        JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                        WHERE c.tenant_id=%s AND c.kb_id=%s AND c.index_revision=%s AND c.doc_id=%s
                          AND c.chunk_index BETWEEN %s AND %s
                          AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                        ORDER BY c.chunk_index ASC""",
                        (tenant_id, kb_id, revision, doc_id, max(0, chunk_index - radius), chunk_index + radius),
                    )
                else:
                    cur.execute(
                        """SELECT c.chunk_id,c.chunk_index,c.text
                        FROM chunks c
                        JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                        WHERE c.tenant_id=%s AND c.kb_id=%s AND c.doc_id=%s
                          AND c.chunk_index BETWEEN %s AND %s
                          AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                        ORDER BY c.chunk_index ASC""",
                        (tenant_id, kb_id, doc_id, max(0, chunk_index - radius), chunk_index + radius),
                    )
                neighbors = [dict(row) for row in cur.fetchall()]
                if not neighbors:
                    expanded.append(item)
                    continue
                item["matched_text"] = str(item.get("text") or "")
                item["text"] = "\n\n".join(
                    self._strip_nul(str(row.get("text") or "")) for row in neighbors if str(row.get("text") or "")
                )
                item["context_chunk_ids"] = [str(row.get("chunk_id") or "") for row in neighbors]
                item["context_range"] = [
                    int(neighbors[0].get("chunk_index") or 0),
                    int(neighbors[-1].get("chunk_index") or 0),
                ]
                item["context_window"] = radius
                expanded.append(item)
        return expanded

    def refresh_rag_index_manifest(self, tenant_id: str, kb_id: str) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(
                """SELECT active_index_revision FROM knowledge_bases
                WHERE tenant_id=%s AND kb_id=%s FOR UPDATE""",
                (tenant_id, kb_id),
            )
            kb_row = cur.fetchone()
            if not kb_row:
                conn.rollback()
                return self.get_rag_index_manifest(tenant_id, kb_id)
            active_revision = str(kb_row.get("active_index_revision") or self.rag_index_revision)
            cur.execute(
                """SELECT EXISTS(
                    SELECT 1 FROM rag_index_chunks
                    WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s LIMIT 1
                ) AS has_snapshot""",
                (tenant_id, kb_id, active_revision),
            )
            has_snapshot = bool((cur.fetchone() or {}).get("has_snapshot"))
            if has_snapshot:
                cur.execute(
                    """SELECT count(DISTINCT c.doc_id)::int AS document_count,
                        count(c.chunk_id)::int AS chunk_count,
                        count(c.chunk_id) FILTER (WHERE c.lexical_token_count>0)::int AS lexical_chunk_count
                    FROM rag_index_chunks c
                    JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                    WHERE c.tenant_id=%s AND c.kb_id=%s AND c.index_revision=%s
                      AND d.status='INDEXED' AND d.frozen=FALSE""",
                    (tenant_id, kb_id, active_revision),
                )
            else:
                cur.execute(
                    """SELECT count(DISTINCT d.doc_id)::int AS document_count,
                        count(c.chunk_id)::int AS chunk_count,
                        count(c.chunk_id) FILTER (WHERE c.lexical_token_count>0)::int AS lexical_chunk_count
                    FROM documents d
                    LEFT JOIN chunks c ON c.tenant_id=d.tenant_id AND c.kb_id=d.kb_id AND c.doc_id=d.doc_id
                    WHERE d.tenant_id=%s AND d.kb_id=%s AND d.status='INDEXED' AND d.frozen=FALSE""",
                    (tenant_id, kb_id),
                )
            counts = dict(cur.fetchone() or {})
            collection_name = (
                self._index_collection_name(tenant_id, kb_id, active_revision)
                if has_snapshot else self._collection_name(tenant_id, kb_id)
            )
            cur.execute(
                """INSERT INTO rag_index_manifests
                (tenant_id,kb_id,index_revision,status,parser_id,chunker_id,dense_model_id,
                 sparse_model_id,reranker_id,config_json,document_count,chunk_count,
                 lexical_chunk_count,collection_name,gate_status,quality_report,
                 created_at,activated_at,updated_at)
                VALUES (%s,%s,%s,'ACTIVE','plain-text-v1','character-v1','configured-embedding',
                        'lexical-ngram-v1','',%s::jsonb,%s,%s,%s,%s,'WAIVED',%s::jsonb,
                        NOW(),NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,index_revision) DO UPDATE SET
                    document_count=EXCLUDED.document_count,
                    chunk_count=EXCLUDED.chunk_count,lexical_chunk_count=EXCLUDED.lexical_chunk_count,
                    collection_name=COALESCE(NULLIF(rag_index_manifests.collection_name,''),EXCLUDED.collection_name),
                    config_json=rag_index_manifests.config_json,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    active_revision,
                    json.dumps({"dense": True, "lexical": True, "fusion": "rrf"}),
                    int(counts.get("document_count") or 0),
                    int(counts.get("chunk_count") or 0),
                    int(counts.get("lexical_chunk_count") or 0),
                    collection_name,
                    json.dumps({"mode": "legacy-active-bootstrap", "snapshot": has_snapshot}),
                ),
            )
            conn.commit()
        return self.get_rag_index_manifest(tenant_id, kb_id, active_revision)

    def get_rag_index_manifest(
        self,
        tenant_id: str,
        kb_id: str,
        index_revision: str | None = None,
    ) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            revision = str(index_revision or self.get_active_index_revision(tenant_id, kb_id))
            cur.execute(
                """SELECT tenant_id,kb_id,index_revision,status,parser_id,chunker_id,dense_model_id,
                    sparse_model_id,reranker_id,config_json,document_count,chunk_count,
                    lexical_chunk_count,collection_name,source_revision,build_id,gate_status,
                    quality_report,build_error,built_at,activated_by,created_at,activated_at,updated_at
                FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s
                LIMIT 1""",
                (tenant_id, kb_id, revision),
            )
            row = cur.fetchone()
        if row:
            return dict(row)
        return {
            "tenant_id": tenant_id,
            "kb_id": kb_id,
            "index_revision": revision,
            "status": "ACTIVE",
            "parser_id": "plain-text-v1",
            "chunker_id": "character-v1",
            "dense_model_id": "configured-embedding",
            "sparse_model_id": "",
            "reranker_id": "",
            "config_json": {"dense": True, "lexical": False, "fusion": "none"},
            "document_count": 0,
            "chunk_count": 0,
            "lexical_chunk_count": 0,
        }

    def backfill_rag2_lexical_features(self, batch_size: int = 500) -> dict:
        """Upgrade one bounded batch of legacy chunks without re-embedding them."""
        self._ensure_indexes()
        limit = max(1, min(int(batch_size or 500), 5000))
        touched: set[tuple[str, str]] = set()
        with self._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,chunk_id,text
                FROM chunks
                WHERE index_revision <> %s
                ORDER BY tenant_id,kb_id,doc_id,chunk_index
                LIMIT %s
                FOR UPDATE SKIP LOCKED""",
                (self.rag_index_revision, limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                lexical_tf = lexical_term_frequency(self._strip_nul(row.get("text") or ""))
                cur.execute(
                    """UPDATE chunks SET lexical_terms=%s::text[],lexical_tf=%s::jsonb,
                    lexical_token_count=%s,index_revision=%s,updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_id=%s""",
                    (
                        list(lexical_tf),
                        json.dumps(lexical_tf, ensure_ascii=False),
                        sum(lexical_tf.values()),
                        self.rag_index_revision,
                        row["tenant_id"],
                        row["kb_id"],
                        row["doc_id"],
                        row["chunk_id"],
                    ),
                )
                touched.add((str(row["tenant_id"]), str(row["kb_id"])))
        for tenant_id, kb_id in sorted(touched):
            self.refresh_rag_index_manifest(tenant_id, kb_id)
        return {"processed": len(rows), "knowledge_bases": len(touched), "has_more": len(rows) >= limit}

    def backfill_parent_child_structure(
        self,
        batch_documents: int = 100,
        *,
        tenant_id: str | None = None,
        kb_id: str | None = None,
    ) -> dict:
        """Attach deterministic parent contexts to legacy child chunks without re-embedding."""
        self._ensure_indexes()
        limit = max(1, min(int(batch_documents or 100), 500))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT c.tenant_id,c.kb_id,c.doc_id,d.filename
                FROM chunks c
                JOIN documents d ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                WHERE c.parent_id='' AND d.status='INDEXED'
                  AND (%s::text IS NULL OR c.tenant_id=%s)
                  AND (%s::text IS NULL OR c.kb_id=%s)
                GROUP BY c.tenant_id,c.kb_id,c.doc_id,d.filename
                ORDER BY c.tenant_id,c.kb_id,c.doc_id LIMIT %s""",
                (tenant_id, tenant_id, kb_id, kb_id, limit),
            )
            documents = [dict(row) for row in cur.fetchall()]
        chunk_count = 0
        touched: set[tuple[str, str]] = set()
        for document in documents:
            tenant_id = str(document["tenant_id"])
            kb_id = str(document["kb_id"])
            doc_id = str(document["doc_id"])
            chunks = self.list_document_chunks_for_embedding(tenant_id, kb_id, doc_id)
            specs: list[dict] = []
            groups: list[list[dict]] = []
            current: list[dict] = []
            current_chars = 0
            for chunk in chunks:
                text = self._strip_nul(str(chunk.get("text") or ""))
                if current and current_chars + len(text) + 2 > 1600:
                    groups.append(current)
                    current = []
                    current_chars = 0
                current.append(chunk)
                current_chars += len(text) + 2
            if current:
                groups.append(current)
            for parent_index, group in enumerate(groups):
                parent_text = "\n\n".join(self._strip_nul(str(item.get("text") or "")) for item in group)
                raw = f"{tenant_id}:{kb_id}:{doc_id}:{parent_index}:{parent_text}"
                parent_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
                for _ in group:
                    specs.append({
                        "parent_id": parent_id,
                        "parent_index": parent_index,
                        "parent_text": parent_text,
                        "section_path": [],
                        "block_type": "paragraph",
                        "page_start": 0,
                        "page_end": 0,
                    })
            self.replace_chunk_structure(
                tenant_id, kb_id, doc_id, specs, str(document.get("filename") or "unknown")
            )
            chunk_count += len(specs)
            touched.add((tenant_id, kb_id))
        return {
            "documents_processed": len(documents),
            "chunks_processed": chunk_count,
            "knowledge_bases": len(touched),
            "has_more": len(documents) >= limit,
        }

    def get_kb_embedding_dimension(self, tenant_id: str, kb_id: str) -> int:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT jsonb_array_length(embedding_json) AS dim
                FROM chunks
                WHERE tenant_id=%s AND kb_id=%s
                  AND jsonb_typeof(embedding_json)='array'
                  AND jsonb_array_length(embedding_json)>0
                ORDER BY updated_at DESC
                LIMIT 1""",
                (tenant_id, kb_id),
            )
            row = cur.fetchone() or {}
        try:
            return int(row.get("dim") or 0)
        except Exception:
            return 0

    def list_documents(self, tenant_id: str, kb_id: str, limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 500))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,status,chunk_count,embedded_count,embedding_dirty,
                source_object_bucket,source_object_key,source_object_sha256,source_object_size_bytes,
                source_content_type,extracted_object_key,extracted_text_sha256,
                tags_json,frozen,
                summary_overview,summary_text,summary_dirty,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at,text_size_bytes,word_count
                FROM documents WHERE tenant_id=%s AND kb_id=%s ORDER BY updated_at DESC LIMIT %s""",
                (tenant_id, kb_id, cap),
            )
            docs = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT
                    doc_id,
                    COUNT(*)::int AS cnt,
                    COUNT(*) FILTER (WHERE jsonb_typeof(embedding_json)='array' AND jsonb_array_length(embedding_json)>0)::int AS embedded
                FROM chunks WHERE tenant_id=%s AND kb_id=%s GROUP BY doc_id""",
                (tenant_id, kb_id),
            )
            chunk_stats = {
                row["doc_id"]: {
                    "chunk_count": int(row["cnt"]),
                    "embedded_count": int(row["embedded"]),
                }
                for row in cur.fetchall()
            }
        for doc in docs:
            stats = chunk_stats.get(doc["doc_id"], {"chunk_count": 0, "embedded_count": 0})
            actual = int(stats["chunk_count"])
            embedded_actual = int(stats["embedded_count"])
            status = str(doc.get("status") or "").upper()
            # During active pipeline stages, keep preview counters from documents table.
            # Chunks table may still hold previous generation until indexing replacement finishes.
            if status in {"PARSING", "CHUNKING", "EMBEDDING", "INDEXING", "UPLOADING"}:
                continue
            if int(doc.get("chunk_count") or 0) != actual or int(doc.get("embedded_count") or 0) != embedded_actual:
                self._update_document_counters(tenant_id, kb_id, doc["doc_id"], actual, embedded_actual, embedded_actual < actual)
                doc["chunk_count"] = actual
                doc["embedded_count"] = embedded_actual
                doc["embedding_dirty"] = embedded_actual < actual
        return docs

    def describe_documents(self, tenant_id: str, kb_id: str, doc_ids: list[str]) -> list[dict]:
        """Fetch an exact bounded document set without depending on list pagination."""
        self._ensure_indexes()
        normalized = list(dict.fromkeys(self._strip_nul(value).strip() for value in doc_ids if self._strip_nul(value).strip()))
        if not normalized:
            return []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT doc_id,filename,status,chunk_count
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=ANY(%s::text[])""",
                (tenant_id, kb_id, normalized),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_chunks(self, tenant_id: str, kb_id: str, doc_id: str, limit: int = 200) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,embedding_json,metadata,updated_at,created_at
                ,frozen
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC LIMIT %s""",
                (tenant_id, kb_id, doc_id, cap),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_all_document_chunks(self, tenant_id: str, kb_id: str, doc_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,embedding_json,metadata,updated_at,created_at,frozen
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC""",
                (tenant_id, kb_id, doc_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def build_document_text(self, tenant_id: str, kb_id: str, doc_id: str) -> str:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT text FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC", (tenant_id, kb_id, doc_id))
            parts = [str(row["text"] or "") for row in cur.fetchall()]
        return "\n".join([p for p in parts if p])

    def delete_document(self, tenant_id: str, kb_id: str, doc_id: str) -> bool:
        self._ensure_indexes()
        document = self.get_document(tenant_id, kb_id, doc_id)
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            deleted = cur.rowcount > 0
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
        try:
            self._delete_qdrant_document_points(tenant_id, kb_id, doc_id)
        except Exception as exc:
            # SQL deletion is the logical authority. Persist the remote cleanup
            # obligation so a transient Qdrant failure does not turn into an
            # unaudited permanent orphan.
            self.enqueue_cross_store_repair(
                store_name="QDRANT",
                operation="DELETE_DOCUMENT_POINTS",
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                error_message=str(exc),
            )
        if document:
            bucket = str(document.get("source_object_bucket") or "")
            keys = {
                str(document.get("source_object_key") or ""),
                str(document.get("extracted_object_key") or ""),
            }
            for key in sorted(item for item in keys if item):
                try:
                    self._object_store().delete_object(bucket, key)
                except Exception as exc:
                    self.enqueue_cross_store_repair(
                        store_name="MINIO",
                        operation="DELETE_OBJECT",
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        doc_id=doc_id,
                        error_message=str(exc),
                        payload={"bucket": bucket, "key": key},
                    )
        self.recompute_kb_stats(tenant_id, kb_id)
        return deleted

    def _delete_qdrant_document_points(self, tenant_id: str, kb_id: str, doc_id: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT collection_name FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND collection_name<>''""",
                (tenant_id, kb_id),
            )
            collection_names = {str(row.get("collection_name") or "") for row in cur.fetchall()}
        collection_names.add(self._collection_name(tenant_id, kb_id))
        for collection_name in sorted(name for name in collection_names if name):
            if not self.qdrant.collection_exists(collection_name=collection_name):
                continue
            self.qdrant.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                            qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
            )

    def find_document_move_repair(
        self,
        tenant_id: str,
        source_kb_id: str,
        target_kb_id: str,
        doc_id: str,
    ) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM cross_store_repairs
                WHERE store_name='QDRANT' AND operation='MOVE_DOCUMENT_POINTS'
                  AND tenant_id=%s AND kb_id=%s AND doc_id=%s
                  AND payload_json->>'target_kb_id'=%s
                ORDER BY repair_id DESC LIMIT 1""",
                (tenant_id, source_kb_id, doc_id, target_kb_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def _reconcile_document_move(self, item: dict, *, _collection_lock_held: bool = False) -> None:
        payload = item.get("payload_json") or {}
        tenant_id = str(item.get("tenant_id") or "")
        doc_id = str(item.get("doc_id") or "")
        source_kb_id = str(payload.get("source_kb_id") or item.get("kb_id") or "")
        target_kb_id = str(payload.get("target_kb_id") or "")
        original_status = self._strip_nul(str(payload.get("original_status") or "INDEXED"))
        if not tenant_id or not source_kb_id or not target_kb_id or not doc_id:
            raise RuntimeError("document move repair identity is incomplete")
        if not self.get_document(tenant_id, target_kb_id, doc_id):
            raise RuntimeError("document move target is missing")

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_id,chunk_index,text,embedding_json,metadata
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s
                ORDER BY chunk_index ASC""",
                (tenant_id, target_kb_id, doc_id),
            )
            chunks = [dict(row) for row in cur.fetchall()]
        records: list[ChunkRecord] = []
        for chunk in chunks:
            embedding = chunk.get("embedding_json") or []
            if not isinstance(embedding, list) or not embedding:
                continue
            records.append(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=target_kb_id,
                    doc_id=doc_id,
                    chunk_id=str(chunk.get("chunk_id") or ""),
                    chunk_index=int(chunk.get("chunk_index") or 0),
                    text=self._strip_nul(str(chunk.get("text") or "")),
                    embedding=[float(value) for value in embedding],
                    metadata=chunk.get("metadata") or {},
                )
            )

        def reconcile_qdrant() -> None:
            target_collection = self._collection_name(tenant_id, target_kb_id)
            if records:
                self._replace_qdrant_document_chunks(
                    target_collection,
                    tenant_id,
                    target_kb_id,
                    doc_id,
                    records,
                )
            else:
                self._delete_qdrant_document_points(tenant_id, target_kb_id, doc_id)
            self._delete_qdrant_document_points(tenant_id, source_kb_id, doc_id)

        if _collection_lock_held:
            reconcile_qdrant()
        else:
            collection_names = [
                self._collection_name(tenant_id, source_kb_id),
                self._collection_name(tenant_id, target_kb_id),
            ]
            with self._qdrant_collection_locks(collection_names):
                reconcile_qdrant()

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents SET status=%s,updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND status='MOVING'""",
                (original_status, tenant_id, target_kb_id, doc_id),
            )
        self.recompute_kb_stats(tenant_id, source_kb_id)
        self.recompute_kb_stats(tenant_id, target_kb_id)

    def _execute_cross_store_repair_item(
        self,
        item: dict,
        *,
        _collection_lock_held: bool = False,
    ) -> None:
        if item["store_name"] == "QDRANT" and item["operation"] == "DELETE_DOCUMENT_POINTS":
            self._delete_qdrant_document_points(item["tenant_id"], item["kb_id"], item["doc_id"])
        elif item["store_name"] == "QDRANT" and item["operation"] == "DELETE_COLLECTION":
            payload = item.get("payload_json") or {}
            collection_name = str(payload.get("collection_name") or self._collection_name(item["tenant_id"], item["kb_id"]))
            if self.qdrant.collection_exists(collection_name=collection_name):
                self.qdrant.delete_collection(collection_name=collection_name)
        elif item["store_name"] == "QDRANT" and item["operation"] == "MOVE_DOCUMENT_POINTS":
            self._reconcile_document_move(item, _collection_lock_held=_collection_lock_held)
        elif item["store_name"] == "MINIO" and item["operation"] == "DELETE_OBJECT":
            payload = item.get("payload_json") or {}
            self._object_store().delete_object(str(payload.get("bucket") or ""), str(payload.get("key") or ""))
        else:
            raise RuntimeError(f"unsupported repair operation: {item['store_name']}/{item['operation']}")

    def repair_cross_store_operation(self, repair_id: int, *, _collection_lock_held: bool = False) -> bool:
        """Claim and execute one repair obligation; safe to repeat after crashes."""
        self._ensure_indexes()
        normalized_id = int(repair_id)
        with self._conn() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM cross_store_repairs WHERE repair_id=%s FOR UPDATE",
                    (normalized_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"cross-store repair not found: {normalized_id}")
                item = dict(row)
                if item["status"] == "SUCCEEDED":
                    return True
                if item["status"] == "IN_PROGRESS":
                    return False
                cur.execute(
                    """UPDATE cross_store_repairs
                    SET status='IN_PROGRESS',attempts=attempts+1,updated_at=NOW()
                    WHERE repair_id=%s AND status IN ('PENDING','FAILED')""",
                    (normalized_id,),
                )
                if cur.rowcount != 1:
                    return False
        try:
            self._execute_cross_store_repair_item(item, _collection_lock_held=_collection_lock_held)
        except Exception as exc:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE cross_store_repairs SET status='FAILED',last_error=%s,updated_at=NOW()
                    WHERE repair_id=%s AND status='IN_PROGRESS'""",
                    (self._strip_nul(str(exc))[:2000], normalized_id),
                )
            return False
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE cross_store_repairs
                SET status='SUCCEEDED',last_error='',completed_at=NOW(),updated_at=NOW()
                WHERE repair_id=%s AND status='IN_PROGRESS'""",
                (normalized_id,),
            )
        return True

    def enqueue_cross_store_repair(
        self,
        store_name: str,
        operation: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str = "",
        error_message: str = "",
        payload: dict | None = None,
    ) -> int:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cross_store_repairs
                (store_name,operation,tenant_id,kb_id,doc_id,status,attempts,last_error,payload_json,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'PENDING',0,%s,%s::jsonb,NOW(),NOW()) RETURNING repair_id""",
                (
                    self._strip_nul(store_name).upper(),
                    self._strip_nul(operation).upper(),
                    tenant_id,
                    kb_id,
                    doc_id,
                    self._strip_nul(error_message)[:2000],
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            return int(cur.fetchone()["repair_id"])

    def list_cross_store_repairs(self, limit: int = 100, status: str | None = None) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 500))
        with self._conn() as conn, conn.cursor() as cur:
            if status:
                cur.execute(
                    """SELECT * FROM cross_store_repairs WHERE status=%s
                    ORDER BY updated_at ASC, repair_id ASC LIMIT %s""",
                    (self._strip_nul(status).upper(), cap),
                )
            else:
                cur.execute(
                    "SELECT * FROM cross_store_repairs ORDER BY updated_at ASC, repair_id ASC LIMIT %s",
                    (cap,),
                )
            return [dict(row) for row in cur.fetchall()]

    def get_cross_store_repair(self, repair_id: int) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM cross_store_repairs WHERE repair_id=%s", (int(repair_id),))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_cross_store_repair_stats(self) -> dict:
        """Return global operational health inputs for the platform repair ledger."""
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                  COUNT(*)::int AS total,
                  COUNT(*) FILTER (WHERE status='PENDING')::int AS pending,
                  COUNT(*) FILTER (WHERE status='IN_PROGRESS')::int AS in_progress,
                  COUNT(*) FILTER (WHERE status='SUCCEEDED')::int AS succeeded,
                  COUNT(*) FILTER (WHERE status='FAILED')::int AS failed,
                  COALESCE(SUM(attempts),0)::bigint AS total_attempts,
                  COALESCE(MAX(attempts),0)::int AS max_attempts,
                  MIN(created_at) FILTER (WHERE status IN ('PENDING','IN_PROGRESS','FAILED')) AS oldest_open_at,
                  COALESCE(EXTRACT(EPOCH FROM (
                    NOW() - MIN(created_at) FILTER (WHERE status IN ('PENDING','IN_PROGRESS','FAILED'))
                  )),0)::double precision AS oldest_open_age_seconds,
                  MAX(completed_at) FILTER (WHERE status='SUCCEEDED') AS last_succeeded_at,
                  MAX(updated_at) FILTER (WHERE status='FAILED') AS last_failed_at
                FROM cross_store_repairs"""
            )
            row = dict(cur.fetchone() or {})
            cur.execute(
                """SELECT store_name,status,COUNT(*)::int AS count
                FROM cross_store_repairs GROUP BY store_name,status
                ORDER BY store_name,status"""
            )
            breakdown = [dict(item) for item in cur.fetchall()]
        pending = int(row.get("pending") or 0)
        in_progress = int(row.get("in_progress") or 0)
        succeeded = int(row.get("succeeded") or 0)
        failed = int(row.get("failed") or 0)
        terminal = succeeded + failed
        return {
            "total": int(row.get("total") or 0),
            "status_counts": {
                "PENDING": pending,
                "IN_PROGRESS": in_progress,
                "SUCCEEDED": succeeded,
                "FAILED": failed,
            },
            "open_count": pending + in_progress + failed,
            "actionable_count": pending + failed,
            "total_attempts": int(row.get("total_attempts") or 0),
            "max_attempts": int(row.get("max_attempts") or 0),
            "failure_ratio": round(failed / terminal, 6) if terminal else 0.0,
            "oldest_open_at": row.get("oldest_open_at"),
            "oldest_open_age_seconds": max(0.0, round(float(row.get("oldest_open_age_seconds") or 0.0), 3)),
            "last_succeeded_at": row.get("last_succeeded_at"),
            "last_failed_at": row.get("last_failed_at"),
            "by_store_status": breakdown,
        }

    def repair_cross_store_operations(self, limit: int = 50, stale_after_seconds: int = 300) -> dict:
        """Claim and execute durable remote cleanup obligations."""
        self._ensure_indexes()
        cap = max(1, min(int(limit), 200))
        stale_after = max(30, min(int(stale_after_seconds), 86400))
        with self._conn() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """UPDATE cross_store_repairs
                    SET status='FAILED',
                        last_error=CASE WHEN last_error='' THEN 'stale in-progress repair claim recovered'
                          ELSE LEFT(last_error || '; stale in-progress repair claim recovered', 2000) END,
                        updated_at=NOW()
                    WHERE status='IN_PROGRESS'
                      AND updated_at < NOW() - (%s * INTERVAL '1 second')""",
                    (stale_after,),
                )
                stale_recovered = int(cur.rowcount or 0)
                cur.execute(
                    """SELECT * FROM cross_store_repairs
                    WHERE status IN ('PENDING','FAILED')
                    ORDER BY updated_at ASC, repair_id ASC
                    FOR UPDATE SKIP LOCKED LIMIT %s""",
                    (cap,),
                )
                claimed = [dict(row) for row in cur.fetchall()]
                if claimed:
                    cur.execute(
                        """UPDATE cross_store_repairs
                        SET status='IN_PROGRESS',attempts=attempts+1,updated_at=NOW()
                        WHERE repair_id = ANY(%s)""",
                        ([int(row["repair_id"]) for row in claimed],),
                    )

        succeeded = 0
        failed = 0
        for item in claimed:
            repair_id = int(item["repair_id"])
            try:
                self._execute_cross_store_repair_item(item)
            except Exception as exc:
                failed += 1
                with self._conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE cross_store_repairs SET status='FAILED',last_error=%s,updated_at=NOW()
                        WHERE repair_id=%s AND status='IN_PROGRESS'""",
                        (self._strip_nul(str(exc))[:2000], repair_id),
                    )
            else:
                succeeded += 1
                with self._conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE cross_store_repairs
                        SET status='SUCCEEDED',last_error='',completed_at=NOW(),updated_at=NOW()
                        WHERE repair_id=%s AND status='IN_PROGRESS'""",
                        (repair_id,),
                    )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*)::int AS count FROM cross_store_repairs WHERE status IN ('PENDING','FAILED')")
            remaining = int((cur.fetchone() or {}).get("count") or 0)
        return {
            "claimed": len(claimed),
            "succeeded": succeeded,
            "failed": failed,
            "remaining": remaining,
            "stale_recovered": stale_recovered,
        }

    def recompute_kb_stats(self, tenant_id: str, kb_id: str) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*)::int AS document_count,
                COALESCE(SUM(text_size_bytes),0)::bigint AS total_size_bytes,
                COALESCE(SUM(word_count),0)::bigint AS total_words
                FROM documents WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            doc_stats = cur.fetchone() or {"document_count": 0, "total_size_bytes": 0, "total_words": 0}
            cur.execute("SELECT COUNT(*)::int AS total_chunks FROM chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            chunk_stats = cur.fetchone() or {"total_chunks": 0}
            cur.execute(
                """SELECT
                    summary,
                    summary_updated_at
                FROM knowledge_bases
                WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            kb_row = cur.fetchone() or {"summary": "", "summary_updated_at": None}
            cur.execute(
                """SELECT
                    COUNT(*)::int AS total_docs,
                    COUNT(*) FILTER (WHERE COALESCE(BTRIM(summary_text), '') <> '')::int AS summarized_docs,
                    BOOL_OR(summary_dirty) AS any_summary_dirty,
                    MAX(updated_at) AS max_doc_updated_at
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            summary_row = cur.fetchone() or {
                "total_docs": 0,
                "summarized_docs": 0,
                "any_summary_dirty": False,
                "max_doc_updated_at": None,
            }
            stats = {
                "document_count": int(doc_stats.get("document_count") or 0),
                "total_size_bytes": int(doc_stats.get("total_size_bytes") or 0),
                "total_chunks": int(chunk_stats.get("total_chunks") or 0),
                "total_words": int(doc_stats.get("total_words") or 0),
            }
            kb_summary = self._strip_nul(kb_row.get("summary", ""))
            kb_summary_updated_at = kb_row.get("summary_updated_at")
            total_docs = int(summary_row.get("total_docs") or 0)
            summarized_docs = int(summary_row.get("summarized_docs") or 0)
            any_summary_dirty = bool(summary_row.get("any_summary_dirty") or False)
            max_doc_updated_at = summary_row.get("max_doc_updated_at")
            if total_docs <= 0:
                # Empty KB: clear stale summary and disable resummarize.
                kb_summary = ""
                kb_summary_dirty = False
                next_embedding = "[]"
                kb_summary_updated_at = None
            else:
                has_kb_summary = bool(kb_summary.strip())
                changed_after_kb_summary = bool(
                    kb_summary_updated_at is None
                    or (max_doc_updated_at is not None and max_doc_updated_at > kb_summary_updated_at)
                )
                missing_doc_summary = summarized_docs < total_docs
                kb_summary_dirty = (not has_kb_summary) or any_summary_dirty or missing_doc_summary or changed_after_kb_summary
                next_embedding = None
            cur.execute(
                """UPDATE knowledge_bases
                SET stats=%s::jsonb,
                    summary=%s,
                    summary_embedding_json=COALESCE(%s::jsonb, summary_embedding_json),
                    summary_dirty=%s,
                    summary_updated_at=%s,
                    updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s""",
                (
                    json.dumps(stats, ensure_ascii=False),
                    kb_summary,
                    next_embedding,
                    kb_summary_dirty,
                    kb_summary_updated_at,
                    tenant_id,
                    kb_id,
                ),
            )
        return stats

    def create_task_record(
        self,
        task_id: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        task_type: str,
        stage: str,
        filename: str | None = None,
        batch_id: str | None = None,
        batch_name: str | None = None,
        batch_is_parent: bool = False,
    ) -> bool:
        self._ensure_indexes()
        safe_task_id = self._strip_nul(task_id).strip()
        safe_tenant_id = self._strip_nul(tenant_id).strip()
        safe_kb_id = self._strip_nul(kb_id).strip()
        safe_doc_id = self._strip_nul(doc_id).strip()
        safe_task_type = self._strip_nul(task_type).strip().upper()
        safe_stage = self._strip_nul(stage).strip().upper()
        if not safe_task_id or not safe_tenant_id or not safe_kb_id or not safe_task_type or not safe_stage:
            raise ValueError("task identity and initial stage must be non-empty")
        safe_filename = self._strip_nul(filename)
        safe_batch_id = self._strip_nul(batch_id)
        safe_batch_name = self._strip_nul(batch_name)
        created = False
        with self._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (task_id,tenant_id,kb_id,doc_id,task_type,status,stage,filename,batch_id,batch_name,batch_is_parent,progress_current,progress_total,progress_pct,cancel_requested,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s,0,0,0,FALSE,NOW(),NOW())
                ON CONFLICT (task_id) DO NOTHING
                RETURNING task_id""",
                (
                    safe_task_id, safe_tenant_id, safe_kb_id, safe_doc_id, safe_task_type, safe_stage,
                    safe_filename, safe_batch_id, safe_batch_name, bool(batch_is_parent),
                ),
            )
            created = cur.fetchone() is not None
            if not created:
                cur.execute(
                    "SELECT tenant_id,kb_id,doc_id,task_type FROM tasks WHERE task_id=%s FOR UPDATE",
                    (safe_task_id,),
                )
                existing = cur.fetchone()
                identity = (safe_tenant_id, safe_kb_id, safe_doc_id, safe_task_type)
                existing_identity = tuple(str((existing or {}).get(key) or "") for key in ("tenant_id", "kb_id", "doc_id", "task_type"))
                if existing is None or existing_identity != identity:
                    raise TaskIdentityConflict("task id already belongs to a different immutable task identity")
        if created:
            self.append_task_event(
                safe_task_id, safe_tenant_id, safe_kb_id, safe_doc_id, "TASK_CREATED", safe_stage,
                {"task_type": safe_task_type, "batch_id": safe_batch_id, "batch_name": safe_batch_name, "batch_is_parent": bool(batch_is_parent)},
            )
        if created and safe_batch_id and not batch_is_parent:
            self.refresh_task_batch(safe_batch_id, tenant_id=safe_tenant_id, kb_id=safe_kb_id)
        return created

    def update_task_record(self, task_id: str, status: str, stage: str, error_message: str | None = None) -> None:
        self._ensure_indexes()
        safe_status = self._strip_nul(status).strip().upper()
        safe_stage = self._strip_nul(stage).strip().upper()
        allowed_statuses = {"PENDING", "RUNNING", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED"}
        transitions = {
            "PENDING": {"PENDING", "RUNNING", "CANCELLING", "FAILED", "CANCELLED"},
            "RUNNING": {"RUNNING", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED"},
            "CANCELLING": {"CANCELLING", "FAILED", "CANCELLED"},
            "SUCCEEDED": {"SUCCEEDED"},
            "FAILED": {"FAILED"},
            "CANCELLED": {"CANCELLED"},
        }
        if safe_status not in allowed_statuses or not safe_stage:
            raise ValueError("task status or stage is invalid")
        batch_row = None
        changed = False
        with self._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT status,stage,error_message FROM tasks WHERE task_id=%s FOR UPDATE", (task_id,))
            current = cur.fetchone()
            if current is None:
                raise TaskNotFound("task not found")
            current_status = str(current.get("status") or "").upper()
            if current_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                if current_status != safe_status or str(current.get("stage") or "") != safe_stage or current.get("error_message") != error_message:
                    raise InvalidTaskStateTransition(f"terminal task {current_status} is immutable")
                return
            if safe_status not in transitions.get(current_status, set()):
                raise InvalidTaskStateTransition(f"task status cannot transition from {current_status} to {safe_status}")
            changed = current_status != safe_status or str(current.get("stage") or "") != safe_stage or current.get("error_message") != error_message
            cur.execute(
                """UPDATE tasks
                SET status=%s, stage=%s, error_message=%s,
                    progress_current=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') THEN progress_total ELSE progress_current END,
                    progress_pct=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') AND progress_total > 0 THEN 100 ELSE progress_pct END,
                    updated_at=NOW()
                WHERE task_id=%s""",
                (safe_status, safe_stage, error_message, safe_status, safe_status, task_id),
            )
            cur.execute("SELECT tenant_id,kb_id,batch_id,batch_is_parent FROM tasks WHERE task_id=%s", (task_id,))
            batch_row = cur.fetchone()
        if safe_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            self.release_task_dedup_locks(task_id)
        if changed:
            self.append_task_event(task_id, "", "", "", "TASK_STATUS", safe_stage, {"status": safe_status, "error_message": error_message})
        if batch_row and batch_row.get("batch_id") and not bool(batch_row.get("batch_is_parent")):
            self.refresh_task_batch(str(batch_row["batch_id"]), tenant_id=str(batch_row["tenant_id"]), kb_id=str(batch_row["kb_id"]))

    def update_task_progress(
        self,
        task_id: str,
        stage: str,
        current: int,
        total: int,
        progress_pct: int | None = None,
    ) -> None:
        self._ensure_indexes()
        c = max(0, int(current or 0))
        t = max(0, int(total or 0))
        if progress_pct is None:
            pct = int((c * 100 / t)) if t > 0 else 0
        else:
            pct = max(0, min(100, int(progress_pct)))
        batch_row = None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET stage=%s,
                    progress_current=GREATEST(progress_current,%s),
                    progress_total=GREATEST(progress_total,%s),
                    progress_pct=GREATEST(progress_pct,%s),
                    updated_at=NOW()
                WHERE task_id=%s AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')""",
                (stage, c, t, pct, task_id),
            )
            cur.execute("SELECT tenant_id,kb_id,batch_id,batch_is_parent FROM tasks WHERE task_id=%s", (task_id,))
            batch_row = cur.fetchone()
        if batch_row and batch_row.get("batch_id") and not bool(batch_row.get("batch_is_parent")):
            self.refresh_task_batch(str(batch_row["batch_id"]), tenant_id=str(batch_row["tenant_id"]), kb_id=str(batch_row["kb_id"]))

    def request_cancel_task(self, task_id: str, tenant_id: str | None = None) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET cancel_requested=TRUE,status='CANCELLING',updated_at=NOW()
                WHERE task_id=%s AND (%s::text IS NULL OR tenant_id=%s) AND status IN ('PENDING','RUNNING','CANCELLING')""",
                (task_id, tenant_id, tenant_id),
            )
            matched = cur.rowcount > 0
        if matched:
            rec = self.get_task(task_id) or {}
            self.append_task_event(task_id, rec.get("tenant_id", ""), rec.get("kb_id", ""), rec.get("doc_id", ""), "CANCEL_REQUESTED", "CANCELLING", {})
        return matched

    def is_cancel_requested(self, task_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT cancel_requested FROM tasks WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
            return bool(row and row.get("cancel_requested"))

    def list_tasks(self, tenant_id: str, kb_id: str | None = None, limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 300))
        with self._conn() as conn, conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                    COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                    tasks.batch_id,tasks.batch_name,tasks.batch_is_parent,
                    progress_current,progress_total,progress_pct,
                    cancel_requested,error_message,tasks.state_owner,tasks.created_at,tasks.updated_at
                    FROM tasks
                    LEFT JOIN documents
                    ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                    WHERE tasks.tenant_id=%s AND tasks.kb_id=%s ORDER BY tasks.updated_at DESC LIMIT %s""",
                    (tenant_id, kb_id, cap),
                )
            else:
                cur.execute(
                    """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                    COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                    tasks.batch_id,tasks.batch_name,tasks.batch_is_parent,
                    progress_current,progress_total,progress_pct,
                    cancel_requested,error_message,tasks.state_owner,tasks.created_at,tasks.updated_at
                    FROM tasks
                    LEFT JOIN documents
                    ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                    WHERE tasks.tenant_id=%s ORDER BY tasks.updated_at DESC LIMIT %s""",
                    (tenant_id, cap),
                )
            return [dict(row) for row in cur.fetchall()]

    def acquire_task_dedup_lock(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        lock_scope: str,
        task_id: str,
        ttl_seconds: int = 21600,
    ) -> dict:
        self._ensure_indexes()
        safe_tenant = self._strip_nul(tenant_id)
        safe_kb = self._strip_nul(kb_id)
        safe_doc = self._strip_nul(doc_id)
        safe_scope = self._strip_nul(lock_scope).upper() or "DEFAULT"
        safe_task = self._strip_nul(task_id)
        ttl = max(60, int(ttl_seconds or 21600))

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM task_dedup_locks WHERE expires_at < NOW()")
            cur.execute(
                """DELETE FROM task_dedup_locks l
                WHERE l.tenant_id=%s AND l.kb_id=%s AND l.doc_id=%s AND l.lock_scope=%s
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.task_id=l.task_id)""",
                (safe_tenant, safe_kb, safe_doc, safe_scope),
            )
            cur.execute(
                """DELETE FROM task_dedup_locks l
                USING tasks t
                WHERE l.task_id=t.task_id
                  AND l.tenant_id=%s AND l.kb_id=%s AND l.doc_id=%s AND l.lock_scope=%s
                  AND (
                    t.status IN ('SUCCEEDED','FAILED','CANCELLED')
                    OR (t.cancel_requested=TRUE AND t.status IN ('PENDING','RUNNING','CANCELLING') AND t.updated_at < (NOW() - INTERVAL '30 seconds'))
                  )""",
                (safe_tenant, safe_kb, safe_doc, safe_scope),
            )
            cur.execute(
                """INSERT INTO task_dedup_locks
                (tenant_id,kb_id,doc_id,lock_scope,task_id,created_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,NOW(),NOW() + (%s || ' seconds')::interval)
                ON CONFLICT (tenant_id,kb_id,doc_id,lock_scope) DO NOTHING""",
                (safe_tenant, safe_kb, safe_doc, safe_scope, safe_task, str(ttl)),
            )
            if int(cur.rowcount or 0) > 0:
                return {"acquired": True, "task_id": safe_task, "lock_scope": safe_scope}
            cur.execute(
                """SELECT l.task_id,t.status,t.stage,t.updated_at
                FROM task_dedup_locks l
                LEFT JOIN tasks t ON t.task_id=l.task_id
                WHERE l.tenant_id=%s AND l.kb_id=%s AND l.doc_id=%s AND l.lock_scope=%s
                LIMIT 1""",
                (safe_tenant, safe_kb, safe_doc, safe_scope),
            )
            row = cur.fetchone() or {}
            return {
                "acquired": False,
                "task_id": self._strip_nul(row.get("task_id")),
                "status": self._strip_nul(row.get("status")),
                "stage": self._strip_nul(row.get("stage")),
                "updated_at": row.get("updated_at"),
                "lock_scope": safe_scope,
            }

    def release_task_dedup_locks(self, task_id: str) -> int:
        self._ensure_indexes()
        safe_task = self._strip_nul(task_id)
        if not safe_task:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM task_dedup_locks WHERE task_id=%s", (safe_task,))
            return int(cur.rowcount or 0)

    def clear_finished_tasks(self, tenant_id: str, kb_id: str | None = None) -> dict:
        self._ensure_indexes()
        terminal_statuses = ("SUCCEEDED", "FAILED", "CANCELLED")
        terminal_stages = (
            "FAILED",
            "CANCELLED",
            "INDEXED",
            "SUMMARIZED",
            "UPLOADED_RAW",
            "CLEANED",
            "MOVED",
            "TAGGED",
            "BATCH_COMPLETED",
            "BATCH_FAILED",
        )
        with self._conn() as conn, conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """SELECT t.task_id
                    FROM tasks t
                    LEFT JOIN documents d
                      ON d.tenant_id=t.tenant_id AND d.kb_id=t.kb_id AND d.doc_id=t.doc_id
                    WHERE t.tenant_id=%s AND t.kb_id=%s
                      AND (
                        t.status = ANY(%s)
                        OR (t.status IN ('PENDING','RUNNING','CANCELLING') AND t.stage = ANY(%s))
                        OR (t.cancel_requested=TRUE AND t.status='CANCELLING')
                        OR (t.cancel_requested=TRUE AND t.status IN ('PENDING','RUNNING','CANCELLING') AND t.updated_at < (NOW() - INTERVAL '30 seconds'))
                        OR (
                          t.status IN ('PENDING','RUNNING','CANCELLING')
                          AND COALESCE(d.status, '') IN ('FAILED','CANCELLED','INDEXED','SUMMARIZED','UPLOADED_RAW','CLEANED','MOVED','TAGGED')
                        )
                      )""",
                    (tenant_id, kb_id, list(terminal_statuses), list(terminal_stages)),
                )
            else:
                cur.execute(
                    """SELECT t.task_id
                    FROM tasks t
                    LEFT JOIN documents d
                      ON d.tenant_id=t.tenant_id AND d.kb_id=t.kb_id AND d.doc_id=t.doc_id
                    WHERE t.tenant_id=%s
                      AND (
                        t.status = ANY(%s)
                        OR (t.status IN ('PENDING','RUNNING','CANCELLING') AND t.stage = ANY(%s))
                        OR (t.cancel_requested=TRUE AND t.status='CANCELLING')
                        OR (t.cancel_requested=TRUE AND t.status IN ('PENDING','RUNNING','CANCELLING') AND t.updated_at < (NOW() - INTERVAL '30 seconds'))
                        OR (
                          t.status IN ('PENDING','RUNNING','CANCELLING')
                          AND COALESCE(d.status, '') IN ('FAILED','CANCELLED','INDEXED','SUMMARIZED','UPLOADED_RAW','CLEANED','MOVED','TAGGED')
                        )
                      )""",
                    (tenant_id, list(terminal_statuses), list(terminal_stages)),
                )
            task_ids = [str(row["task_id"]) for row in cur.fetchall()]
            if not task_ids:
                return {"deleted_tasks": 0, "deleted_events": 0}

            cur.execute("DELETE FROM task_dedup_locks WHERE task_id = ANY(%s)", (task_ids,))
            cur.execute("DELETE FROM task_events WHERE task_id = ANY(%s)", (task_ids,))
            deleted_events = int(cur.rowcount or 0)
            cur.execute("DELETE FROM tasks WHERE task_id = ANY(%s)", (task_ids,))
            deleted_tasks = int(cur.rowcount or 0)
            cur.execute(
                """DELETE FROM task_batches b
                WHERE b.tenant_id=%s
                  AND NOT EXISTS (
                    SELECT 1 FROM tasks t
                    WHERE t.tenant_id=b.tenant_id AND t.batch_id=b.batch_id
                  )""",
                (tenant_id,),
            )
            return {"deleted_tasks": deleted_tasks, "deleted_events": deleted_events}

    def create_task_batch(
        self,
        *,
        batch_id: str,
        tenant_id: str,
        kb_id: str,
        operation: str,
        batch_name: str,
        params: dict,
        total_items: int,
        created_by: str,
        parent_task_id: str,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO task_batches (
                    batch_id,tenant_id,kb_id,operation,batch_name,params_json,parent_task_id,
                    total_items,succeeded_items,failed_items,running_items,status,created_by,created_at,updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,0,0,%s,'RUNNING',%s,NOW(),NOW())
                ON CONFLICT (batch_id) DO UPDATE SET
                    operation=EXCLUDED.operation,batch_name=EXCLUDED.batch_name,params_json=EXCLUDED.params_json,
                    parent_task_id=EXCLUDED.parent_task_id,total_items=EXCLUDED.total_items,
                    running_items=EXCLUDED.running_items,status='RUNNING',updated_at=NOW()""",
                (
                    self._strip_nul(batch_id),
                    tenant_id,
                    kb_id,
                    self._strip_nul(operation),
                    self._strip_nul(batch_name),
                    json.dumps(params or {}, ensure_ascii=False),
                    self._strip_nul(parent_task_id),
                    int(total_items or 0),
                    int(total_items or 0),
                    self._strip_nul(created_by or "system"),
                ),
            )

    def get_task_batch(self, batch_id: str, tenant_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT batch_id,tenant_id,kb_id,operation,batch_name,params_json,parent_task_id,
                total_items,succeeded_items,failed_items,cancelled_items,running_items,status,created_by,created_at,updated_at
                FROM task_batches WHERE batch_id=%s AND tenant_id=%s""",
                (batch_id, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_batch_tasks(self, batch_id: str, tenant_id: str, limit: int = 300) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                tasks.batch_id,tasks.batch_name,tasks.batch_is_parent,
                progress_current,progress_total,progress_pct,
                cancel_requested,error_message,tasks.state_owner,tasks.created_at,tasks.updated_at
                FROM tasks
                LEFT JOIN documents
                ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                WHERE tasks.tenant_id=%s AND tasks.batch_id=%s
                ORDER BY tasks.batch_is_parent DESC, tasks.updated_at DESC
                LIMIT %s""",
                (tenant_id, batch_id, cap),
            )
            return [dict(row) for row in cur.fetchall()]

    def refresh_task_batch(self, batch_id: str, *, tenant_id: str, kb_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                    COUNT(*)::int AS total_items,
                    COUNT(*) FILTER (WHERE status='SUCCEEDED')::int AS succeeded_items,
                    COUNT(*) FILTER (WHERE status='FAILED')::int AS failed_items,
                    COUNT(*) FILTER (WHERE status='CANCELLED')::int AS cancelled_items,
                    COUNT(*) FILTER (WHERE status IN ('PENDING','RUNNING','CANCELLING'))::int AS running_items
                FROM tasks
                WHERE tenant_id=%s AND kb_id=%s AND batch_id=%s AND batch_is_parent=FALSE""",
                (tenant_id, kb_id, batch_id),
            )
            agg = cur.fetchone() or {"total_items": 0, "succeeded_items": 0, "failed_items": 0, "running_items": 0}
            total_items = int(agg.get("total_items") or 0)
            succeeded_items = int(agg.get("succeeded_items") or 0)
            failed_items = int(agg.get("failed_items") or 0)
            cancelled_items = int(agg.get("cancelled_items") or 0)
            running_items = int(agg.get("running_items") or 0)
            completed = succeeded_items + failed_items + cancelled_items
            progress_pct = int((completed * 100 / total_items)) if total_items > 0 else 0
            if running_items > 0:
                status = "RUNNING"
                stage = "BATCH_RUNNING"
            elif failed_items > 0:
                status = "FAILED"
                stage = "BATCH_FAILED"
            elif cancelled_items > 0:
                status = "CANCELLED"
                stage = "BATCH_CANCELLED"
            else:
                status = "SUCCEEDED"
                stage = "BATCH_COMPLETED"
            cur.execute(
                """UPDATE task_batches
                SET total_items=%s,succeeded_items=%s,failed_items=%s,cancelled_items=%s,running_items=%s,status=%s,updated_at=NOW()
                WHERE batch_id=%s AND tenant_id=%s
                RETURNING parent_task_id""",
                (total_items, succeeded_items, failed_items, cancelled_items, running_items, status, batch_id, tenant_id),
            )
            row = cur.fetchone() or {}
            parent_task_id = str(row.get("parent_task_id") or "")
            if parent_task_id:
                cur.execute(
                    """UPDATE tasks
                    SET status=%s, stage=%s, progress_current=%s, progress_total=%s, progress_pct=%s,
                        error_message=%s, updated_at=NOW()
                    WHERE task_id=%s""",
                    (
                        status,
                        stage,
                        completed,
                        total_items,
                        progress_pct,
                        (
                            f"{failed_items} 项失败"
                            if failed_items > 0
                            else (f"{cancelled_items} 项取消" if cancelled_items > 0 else None)
                        ),
                        parent_task_id,
                    ),
                )
        return {
            "batch_id": batch_id,
            "total_items": total_items,
            "succeeded_items": succeeded_items,
            "failed_items": failed_items,
            "cancelled_items": cancelled_items,
            "running_items": running_items,
            "status": status,
            "progress_pct": progress_pct,
        }

    def get_task(self, task_id: str, tenant_id: str | None = None) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                tasks.batch_id,tasks.batch_name,tasks.batch_is_parent,
                progress_current,progress_total,progress_pct,
                cancel_requested,error_message,tasks.state_owner,tasks.created_at,tasks.updated_at
                FROM tasks
                LEFT JOIN documents
                ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                WHERE tasks.task_id=%s AND (%s::text IS NULL OR tasks.tenant_id=%s)""",
                (task_id, tenant_id, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def append_task_event(self, task_id: str, tenant_id: str, kb_id: str, doc_id: str, event_type: str, stage: str, payload: dict) -> None:
        self._ensure_indexes()
        rec = self.get_task(task_id)
        if rec is None:
            raise TaskNotFound("task not found")
        for supplied, key in ((tenant_id, "tenant_id"), (kb_id, "kb_id"), (doc_id, "doc_id")):
            if supplied and self._strip_nul(supplied).strip() != str(rec.get(key) or ""):
                raise TaskIdentityConflict(f"task event {key} does not match immutable task identity")
        tenant_id = str(rec.get("tenant_id") or "")
        kb_id = str(rec.get("kb_id") or "")
        doc_id = str(rec.get("doc_id") or "")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO task_events (task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())""",
                (task_id, tenant_id, kb_id, doc_id, event_type, stage, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def list_task_events(self, task_id: str, limit: int = 200, tenant_id: str | None = None) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                FROM (
                    SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                    FROM task_events
                    WHERE task_id=%s AND (%s::text IS NULL OR tenant_id=%s)
                    ORDER BY ts DESC
                    LIMIT %s
                ) t
                ORDER BY ts ASC""",
                (task_id, tenant_id, tenant_id, cap),
            )
            return [dict(row) for row in cur.fetchall()]

    def find_active_doc_task_ids(self, tenant_id: str, kb_id: str, doc_id: str) -> list[str]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT task_id FROM tasks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND status IN ('PENDING','RUNNING','CANCELLING')",
                (tenant_id, kb_id, doc_id),
            )
            return [str(row["task_id"]) for row in cur.fetchall()]

    def find_active_batch_task_ids(self, tenant_id: str, kb_id: str, batch_id: str) -> list[str]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT task_id
                FROM tasks
                WHERE tenant_id=%s AND kb_id=%s AND batch_id=%s AND batch_is_parent=FALSE
                  AND status IN ('PENDING','RUNNING','CANCELLING')""",
                (tenant_id, kb_id, batch_id),
            )
            return [str(row["task_id"]) for row in cur.fetchall()]

    def set_chunk_draft(self, tenant_id: str, kb_id: str, doc_id: str, chunk_id: str, action: str, text: str | None = None) -> None:
        self._ensure_indexes()
        text = self._strip_nul(text)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO chunk_drafts (tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id,chunk_id) DO UPDATE SET
                    action=EXCLUDED.action,text=EXCLUDED.text,updated_at=NOW()""",
                (tenant_id, kb_id, doc_id, chunk_id, action, text),
            )

    def clear_chunk_draft(self, tenant_id: str, kb_id: str, doc_id: str, chunk_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_id=%s", (tenant_id, kb_id, doc_id, chunk_id))
            return cur.rowcount > 0

    def list_chunk_drafts(self, tenant_id: str, kb_id: str, doc_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY updated_at DESC",
                (tenant_id, kb_id, doc_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def replace_text_in_chunk_drafts(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        search_text: str,
        replacement_text: str,
        *,
        case_sensitive: bool = True,
        apply: bool = False,
    ) -> dict:
        """Preview or stage a literal text replacement across every chunk in a document.

        Existing edit drafts are used as the source so a bulk replacement never
        discards pending manual edits. Frozen chunks and chunks already marked for
        deletion are intentionally left untouched.
        """
        self._ensure_indexes()
        needle = self._strip_nul(str(search_text or ""))
        replacement = self._strip_nul(str(replacement_text or ""))
        if not needle:
            raise ValueError("search_text must not be empty")

        pattern = re.compile(re.escape(needle), 0 if case_sensitive else re.IGNORECASE)
        matched_chunks = 0
        match_count = 0
        staged_chunks = 0
        skipped_frozen = 0
        skipped_delete = 0
        skipped_empty = 0
        previews: list[dict] = []

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT c.chunk_id,c.chunk_index,c.text,c.frozen,d.action AS draft_action,d.text AS draft_text
                FROM chunks c
                LEFT JOIN chunk_drafts d
                  ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id
                 AND d.doc_id=c.doc_id AND d.chunk_id=c.chunk_id
                WHERE c.tenant_id=%s AND c.kb_id=%s AND c.doc_id=%s
                ORDER BY c.chunk_index ASC
                FOR UPDATE OF c""",
                (tenant_id, kb_id, doc_id),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                if bool(row.get("frozen")):
                    skipped_frozen += 1
                    continue
                if row.get("draft_action") == "delete":
                    skipped_delete += 1
                    continue
                source = str(row.get("draft_text") if row.get("draft_action") == "edit" else row.get("text") or "")
                updated, occurrences = pattern.subn(lambda _: replacement, source)
                if occurrences <= 0:
                    continue
                matched_chunks += 1
                match_count += occurrences
                if not updated.strip():
                    skipped_empty += 1
                    continue
                if len(previews) < 50:
                    previews.append({
                        "chunk_id": str(row.get("chunk_id") or ""),
                        "chunk_index": int(row.get("chunk_index") or 0),
                        "occurrences": occurrences,
                        "before": source[:240],
                        "after": updated[:240],
                    })
                if apply:
                    cur.execute(
                        """INSERT INTO chunk_drafts (tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at)
                        VALUES (%s,%s,%s,%s,'edit',%s,NOW(),NOW())
                        ON CONFLICT (tenant_id,kb_id,doc_id,chunk_id) DO UPDATE SET
                            action='edit',text=EXCLUDED.text,updated_at=NOW()""",
                        (tenant_id, kb_id, doc_id, row["chunk_id"], updated),
                    )
                    staged_chunks += 1

        return {
            "doc_id": doc_id,
            "total_chunks": len(rows),
            "matched_chunks": matched_chunks,
            "match_count": match_count,
            "staged_chunks": staged_chunks,
            "skipped_frozen": skipped_frozen,
            "skipped_delete": skipped_delete,
            "skipped_empty": skipped_empty,
            "applied": bool(apply),
            "previews": previews,
        }

    def commit_chunk_drafts(self, tenant_id: str, kb_id: str, doc_id: str, embed_fn) -> dict:
        self._ensure_indexes()
        chunks = self.list_all_document_chunks(tenant_id, kb_id, doc_id)
        drafts = {item["chunk_id"]: item for item in self.list_chunk_drafts(tenant_id, kb_id, doc_id)}
        if not drafts:
            return {"doc_id": doc_id, "total_chunks": len(chunks), "reembedded_chunks": 0, "deleted_chunks": 0}

        kept: list[ChunkRecord] = []
        reembedded = 0
        deleted = 0
        for idx, chunk in enumerate(chunks):
            draft = drafts.get(chunk["chunk_id"])
            if draft and draft.get("action") == "delete":
                deleted += 1
                continue
            text = chunk["text"]
            source = (chunk.get("metadata") or {}).get("filename", "unknown")
            if draft and draft.get("action") == "edit":
                text = draft.get("text") or text
                raw = f"{tenant_id}:{kb_id}:{doc_id}:{idx}:{text}"
                chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
                embedding = embed_fn(tenant_id, text)
                reembedded += 1
            else:
                chunk_id = chunk["chunk_id"]
                embedding = chunk.get("embedding_json") or []
                if not embedding:
                    # Backward-safe fallback for legacy rows without vectors.
                    embedding = embed_fn(tenant_id, text)
                    reembedded += 1
            kept.append(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    chunk_index=len(kept),
                    text=text,
                    embedding=embedding,
                    metadata={"filename": source},
                )
            )
        self.replace_chunks(tenant_id, kb_id, doc_id, kept)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute(
                "UPDATE documents SET summary_dirty=TRUE, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (tenant_id, kb_id, doc_id),
            )
        self.recompute_kb_stats(tenant_id, kb_id)
        return {"doc_id": doc_id, "total_chunks": len(kept), "reembedded_chunks": reembedded, "deleted_chunks": deleted}

    def get_upload_settings(self) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value_json FROM system_settings WHERE settings_key='upload_limits'")
            row = cur.fetchone() or {}
            value = row.get("value_json") or {}
            max_file_size_mb = int(value.get("max_file_size_mb", 50))
            if max_file_size_mb < 1:
                max_file_size_mb = 1
            return {"max_file_size_mb": max_file_size_mb}

    def set_upload_settings(self, max_file_size_mb: int, actor_id: str) -> dict:
        self._ensure_indexes()
        payload = {"max_file_size_mb": int(max_file_size_mb)}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_settings (settings_key,value_json,updated_by,created_at,updated_at)
                VALUES ('upload_limits',%s::jsonb,%s,NOW(),NOW())
                ON CONFLICT (settings_key) DO UPDATE SET
                    value_json=EXCLUDED.value_json, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                (json.dumps(payload, ensure_ascii=False), actor_id),
            )
        return self.get_upload_settings()

    def get_risk_event_states(self, event_ids: list[str]) -> dict[str, dict]:
        self._ensure_indexes()
        keys = [str(x or "").strip() for x in (event_ids or []) if str(x or "").strip()]
        if not keys:
            return {}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value_json FROM system_settings WHERE settings_key='risk_event_states'")
            row = cur.fetchone() or {}
            raw = row.get("value_json") or {}
            if not isinstance(raw, dict):
                raw = {}
            out: dict[str, dict] = {}
            for key in keys:
                item = raw.get(key)
                if isinstance(item, dict):
                    out[key] = item
            return out

    def set_risk_event_state(self, *, event_id: str, status: str, actor_id: str, note: str = "") -> dict:
        self._ensure_indexes()
        eid = str(event_id or "").strip()
        if not eid:
            return {}
        st = str(status or "").strip().upper()
        if st not in {"ACKED", "RESOLVED"}:
            st = "ACKED"
        item = {
            "status": st,
            "updated_by": str(actor_id or "system"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "note": str(note or "")[:500],
        }
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value_json FROM system_settings WHERE settings_key='risk_event_states'")
            row = cur.fetchone() or {}
            current = row.get("value_json") or {}
            if not isinstance(current, dict):
                current = {}
            current[eid] = item
            # keep last 5000 states by updated_at desc to cap size
            rows = []
            for key, val in current.items():
                if not isinstance(val, dict):
                    continue
                rows.append((key, str(val.get("updated_at") or ""), val))
            rows.sort(key=lambda x: x[1], reverse=True)
            trimmed = {k: v for k, _, v in rows[:5000]}
            cur.execute(
                """INSERT INTO system_settings (settings_key,value_json,updated_by,created_at,updated_at)
                VALUES ('risk_event_states',%s::jsonb,%s,NOW(),NOW())
                ON CONFLICT (settings_key) DO UPDATE SET
                    value_json=EXCLUDED.value_json, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                (json.dumps(trimmed, ensure_ascii=False), actor_id or "system"),
            )
        return {"event_id": eid, **item}

    @staticmethod
    def _normalize_config_scope(scope_type: str, scope_id: str) -> tuple[str, str]:
        st = (scope_type or "PLATFORM").strip().upper()
        if st not in {"PLATFORM", "TENANT", "USER"}:
            st = "PLATFORM"
        sid = (scope_id or "").strip()
        if not sid:
            sid = "platform" if st == "PLATFORM" else ""
        return st, sid

    @staticmethod
    def _is_secret_config_key(key: str) -> bool:
        k = (key or "").lower()
        secret_tokens = ("api_key", "secret", "password", "private_key")
        if any(token in k for token in secret_tokens):
            return True
        # preserve visibility for non-secret auth strategy switches/thresholds.
        if k.endswith("_token"):
            return True
        return False

    def seed_config_schema(self, defaults: dict, allowed_keys: set[str]) -> dict:
        self._ensure_indexes()
        inserted = 0
        updated = 0
        with self._conn() as conn, conn.cursor() as cur:
            for key in sorted(allowed_keys):
                default_val = defaults.get(key, None)
                data_type = "json"
                if isinstance(default_val, bool):
                    data_type = "bool"
                elif isinstance(default_val, int):
                    data_type = "int"
                elif isinstance(default_val, float):
                    data_type = "float"
                elif isinstance(default_val, str):
                    data_type = "string"
                elif isinstance(default_val, list):
                    data_type = "array"
                elif isinstance(default_val, dict):
                    data_type = "object"
                is_secret = self._is_secret_config_key(key)
                cur.execute("SELECT config_key FROM config_schema WHERE config_key=%s", (key,))
                exists = cur.fetchone()
                payload = json.dumps(default_val, ensure_ascii=False)
                category = "api" if key.startswith("api_") else ("chat" if key.startswith("chat_") else ("memory" if key.startswith("memory_") else "system"))
                if exists:
                    cur.execute(
                        """UPDATE config_schema
                        SET data_type=%s, is_secret=%s, category=%s, default_json=%s::jsonb, updated_at=NOW()
                        WHERE config_key=%s""",
                        (data_type, bool(is_secret), category, payload, key),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO config_schema
                        (config_key,data_type,scope_level,is_secret,category,description,default_json,created_at,updated_at)
                        VALUES (%s,%s,'platform',%s,%s,'',%s::jsonb,NOW(),NOW())""",
                        (key, data_type, bool(is_secret), category, payload),
                    )
                    inserted += 1
        return {"inserted": inserted, "updated": updated}

    def list_config_schema(self) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT config_key,data_type,scope_level,is_secret,category,description,default_json,updated_at
                FROM config_schema
                ORDER BY category, config_key"""
            )
            return [dict(row) for row in (cur.fetchall() or [])]

    def _load_defaults_from_schema(self) -> dict:
        self._ensure_indexes()
        out: dict = {}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT config_key, default_json FROM config_schema")
            for row in (cur.fetchall() or []):
                out[row["config_key"]] = row.get("default_json")
        return out

    def get_config_effective(self, *, scope_type: str, scope_id: str) -> dict:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        chain: list[tuple[str, str]] = [("PLATFORM", "platform")]
        if st == "TENANT" and sid:
            chain.append(("TENANT", sid))
        elif st == "USER" and sid:
            if ":" in sid:
                tenant_id, _ = sid.split(":", 1)
                if tenant_id:
                    chain.append(("TENANT", tenant_id))
            chain.append(("USER", sid))

        item = self._load_defaults_from_schema()
        source = {k: "default" for k in item.keys()}
        with self._conn() as conn, conn.cursor() as cur:
            for c_scope_type, c_scope_id in chain:
                cur.execute(
                    """SELECT config_key,value_json FROM config_values
                    WHERE scope_type=%s AND scope_id=%s""",
                    (c_scope_type, c_scope_id),
                )
                for row in (cur.fetchall() or []):
                    key = row["config_key"]
                    item[key] = row.get("value_json")
                    source[key] = f"{c_scope_type.lower()}:{c_scope_id}"
        return {"scope_type": st, "scope_id": sid, "item": item, "source": source}

    def patch_config_draft(
        self,
        *,
        scope_type: str,
        scope_id: str,
        updates: dict,
        actor_id: str,
        trace_id: str = "",
    ) -> dict:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        changed: list[dict] = []
        with self._conn() as conn, conn.cursor() as cur:
            for key, val in (updates or {}).items():
                cur.execute(
                    "SELECT value_json,version FROM config_values WHERE config_key=%s AND scope_type=%s AND scope_id=%s",
                    (key, st, sid),
                )
                old_row = cur.fetchone() or {}
                old_val = old_row.get("value_json")
                old_ver = int(old_row.get("version") or 0)
                new_ver = old_ver + 1 if old_ver > 0 else 1
                payload = json.dumps(val, ensure_ascii=False)
                cur.execute(
                    """INSERT INTO config_values (config_key,scope_type,scope_id,value_json,version,updated_by,updated_at)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s,NOW())
                    ON CONFLICT (config_key,scope_type,scope_id) DO UPDATE SET
                        value_json=EXCLUDED.value_json, version=config_values.version + 1,
                        updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                    (key, st, sid, payload, new_ver, actor_id),
                )
                cur.execute(
                    """INSERT INTO config_audit_logs
                    (action,scope_type,scope_id,config_key,old_json,new_json,actor_id,trace_id,created_at)
                    VALUES ('PATCH_DRAFT',%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,NOW())""",
                    (
                        st,
                        sid,
                        key,
                        json.dumps(old_val, ensure_ascii=False),
                        payload,
                        actor_id or "system",
                        trace_id or "",
                    ),
                )
                changed.append({"key": key, "old": old_val, "new": val})
        return {"scope_type": st, "scope_id": sid, "changed": changed}

    def publish_config(
        self,
        *,
        scope_type: str,
        scope_id: str,
        changed_keys: list[str],
        actor_id: str,
        trace_id: str = "",
        note: str = "",
        status: str = "PUBLISHED",
        audit_meta: dict | None = None,
    ) -> dict:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        keyset = sorted({k for k in (changed_keys or []) if str(k).strip()})
        changes: list[dict] = []
        snapshot: dict = {}
        # Build full effective snapshot for reliable rollback.
        try:
            snapshot = (self.get_config_effective(scope_type=st, scope_id=sid) or {}).get("item") or {}
        except Exception:
            snapshot = {}
        with self._conn() as conn, conn.cursor() as cur:
            for key in keyset:
                cur.execute(
                    "SELECT value_json,version,updated_at FROM config_values WHERE config_key=%s AND scope_type=%s AND scope_id=%s",
                    (key, st, sid),
                )
                row = cur.fetchone() or {}
                if not row:
                    continue
                changes.append(
                    {
                        "key": key,
                        "value": row.get("value_json"),
                        "version": int(row.get("version") or 1),
                        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else "",
                    }
                )
            cur.execute(
                """INSERT INTO config_releases (scope_type,scope_id,status,apply_status,applied_at,apply_error,note,changes_json,snapshot_json,created_by,created_at)
                VALUES (%s,%s,%s,'PENDING',NULL,'',%s,%s::jsonb,%s::jsonb,%s,NOW())
                RETURNING release_id,created_at,apply_status,applied_at,apply_error""",
                (
                    st,
                    sid,
                    (status or "PUBLISHED").strip().upper(),
                    (note or "")[:500],
                    json.dumps(changes, ensure_ascii=False),
                    json.dumps(snapshot, ensure_ascii=False),
                    actor_id or "system",
                ),
            )
            rel = cur.fetchone() or {}
            cur.execute(
                """INSERT INTO config_audit_logs
                (action,scope_type,scope_id,release_id,config_key,old_json,new_json,actor_id,trace_id,metadata_json,created_at)
                VALUES (%s,%s,%s,%s,'*', 'null'::jsonb, %s::jsonb, %s, %s, %s::jsonb, NOW())""",
                (
                    "PUBLISH",
                    st,
                    sid,
                    int(rel.get("release_id") or 0),
                    json.dumps({"release_id": rel.get("release_id"), "changed_keys": keyset}, ensure_ascii=False),
                    actor_id or "system",
                    trace_id or "",
                    json.dumps(audit_meta or {}, ensure_ascii=False),
                ),
            )
        return {
            "release_id": int(rel.get("release_id") or 0),
            "created_at": rel.get("created_at").isoformat() if rel.get("created_at") else "",
            "scope_type": st,
            "scope_id": sid,
            "status": (status or "PUBLISHED").strip().upper(),
            "apply_status": str(rel.get("apply_status") or "PENDING"),
            "applied_at": rel.get("applied_at").isoformat() if rel.get("applied_at") else "",
            "apply_error": str(rel.get("apply_error") or ""),
            "changed_keys": keyset,
        }

    def rollback_config_release(
        self,
        *,
        release_id: int,
        actor_id: str,
        trace_id: str = "",
    ) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT release_id,scope_type,scope_id,changes_json,snapshot_json
                FROM config_releases
                WHERE release_id=%s""",
                (int(release_id),),
            )
            release = cur.fetchone() or {}
            if not release:
                return {"release_id": int(release_id), "restored": 0}
            st = release["scope_type"]
            sid = release["scope_id"]
            restored = 0
            snapshot = release.get("snapshot_json") or {}
            try:
                current_effective = (self.get_config_effective(scope_type=st, scope_id=sid) or {}).get("item") or {}
            except Exception:
                current_effective = {}
            if isinstance(snapshot, dict) and snapshot:
                try:
                    if json.dumps(current_effective, ensure_ascii=False, sort_keys=True) == json.dumps(snapshot, ensure_ascii=False, sort_keys=True):
                        return {
                            "status": "ok",
                            "from_release_id": int(release_id),
                            "rollback_release_id": 0,
                            "restored": 0,
                            "noop": True,
                            "mode": "switch",
                        }
                except Exception:
                    pass
            # Preferred rollback path: restore full snapshot.
            if isinstance(snapshot, dict) and snapshot:
                for key, val in snapshot.items():
                    payload = json.dumps(val, ensure_ascii=False)
                    cur.execute(
                        """INSERT INTO config_values (config_key,scope_type,scope_id,value_json,version,updated_by,updated_at)
                        VALUES (%s,%s,%s,%s::jsonb,1,%s,NOW())
                        ON CONFLICT (config_key,scope_type,scope_id) DO UPDATE SET
                            value_json=EXCLUDED.value_json, version=config_values.version + 1,
                            updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                        (str(key), st, sid, payload, actor_id or "system"),
                    )
                    restored += 1
            else:
                # Backward compatibility: older releases without snapshot.
                for item in (release.get("changes_json") or []):
                    key = str(item.get("key") or "").strip()
                    if not key:
                        continue
                    payload = json.dumps(item.get("value"), ensure_ascii=False)
                    cur.execute(
                        """INSERT INTO config_values (config_key,scope_type,scope_id,value_json,version,updated_by,updated_at)
                        VALUES (%s,%s,%s,%s::jsonb,1,%s,NOW())
                        ON CONFLICT (config_key,scope_type,scope_id) DO UPDATE SET
                            value_json=EXCLUDED.value_json, version=config_values.version + 1,
                            updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                        (key, st, sid, payload, actor_id or "system"),
                    )
                    restored += 1
            rollback_rel = {}
            cur.execute(
                """INSERT INTO config_audit_logs
                (action,scope_type,scope_id,config_key,old_json,new_json,actor_id,trace_id,created_at)
                VALUES ('ROLLBACK',%s,%s,'*','null'::jsonb,%s::jsonb,%s,%s,NOW())""",
                (
                    st,
                    sid,
                    json.dumps({"from_release_id": int(release_id), "restored": restored}, ensure_ascii=False),
                    actor_id or "system",
                    trace_id or "",
                ),
            )
        return {
            "status": "ok",
            "from_release_id": int(release_id),
            "rollback_release_id": int(rollback_rel.get("release_id") or 0),
            "restored": restored,
            "noop": False,
            "mode": "switch",
        }

    def list_config_releases(self, *, scope_type: str, scope_id: str, limit: int = 50) -> list[dict]:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        cap = max(1, min(int(limit or 50), 500))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT release_id,scope_type,scope_id,status,apply_status,applied_at,apply_error,note,changes_json,snapshot_json,created_by,created_at
                FROM config_releases
                WHERE scope_type=%s AND scope_id=%s
                ORDER BY created_at DESC
                LIMIT %s""",
                (st, sid, cap),
            )
            out = []
            for row in (cur.fetchall() or []):
                item = dict(row)
                item["created_at"] = row["created_at"].isoformat() if row.get("created_at") else ""
                item["applied_at"] = row["applied_at"].isoformat() if row.get("applied_at") else ""
                item["change_count"] = len(row.get("changes_json") or [])
                item["snapshot_json"] = row.get("snapshot_json") if "snapshot_json" in row else {}
                out.append(item)
            return out

    def update_config_release_apply_status(self, *, release_id: int, apply_status: str, apply_error: str = "") -> None:
        self._ensure_indexes()
        rid = int(release_id or 0)
        if rid <= 0:
            return
        status = str(apply_status or "PENDING").strip().upper() or "PENDING"
        err = str(apply_error or "")[:2000]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE config_releases
                SET apply_status=%s,
                    applied_at=CASE WHEN %s IN ('APPLIED','FAILED') THEN NOW() ELSE applied_at END,
                    apply_error=%s
                WHERE release_id=%s""",
                (status, status, err, rid),
            )

    def get_latest_config_release_id(self, *, scope_type: str, scope_id: str) -> int:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT COALESCE(MAX(release_id), 0) AS latest_release_id
                FROM config_releases
                WHERE scope_type=%s AND scope_id=%s""",
                (st, sid),
            )
            row = cur.fetchone() or {}
            try:
                return int(row.get("latest_release_id") or 0)
            except Exception:
                return 0

    def acquire_config_publish_lock(
        self,
        *,
        scope_type: str,
        scope_id: str,
        owner_id: str,
        ttl_seconds: int = 30,
    ) -> dict:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        owner = str(owner_id or "system")
        ttl = max(5, min(int(ttl_seconds or 30), 600))
        lock_token = uuid.uuid4().hex
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO config_publish_locks (scope_type,scope_id,lock_token,owner_id,lock_until,created_at,updated_at)
                VALUES (%s,%s,%s,%s,NOW() + (%s::text || ' seconds')::interval,NOW(),NOW())
                ON CONFLICT (scope_type,scope_id) DO UPDATE
                SET lock_token=EXCLUDED.lock_token,
                    owner_id=EXCLUDED.owner_id,
                    lock_until=EXCLUDED.lock_until,
                    updated_at=NOW()
                WHERE config_publish_locks.lock_until <= NOW() OR config_publish_locks.owner_id = EXCLUDED.owner_id
                RETURNING lock_token,owner_id,lock_until
                """,
                (st, sid, lock_token, owner, str(ttl)),
            )
            row = cur.fetchone() or {}
            if not row:
                cur.execute(
                    "SELECT lock_token,owner_id,lock_until FROM config_publish_locks WHERE scope_type=%s AND scope_id=%s",
                    (st, sid),
                )
                hold = cur.fetchone() or {}
                return {
                    "acquired": False,
                    "lock_token": "",
                    "owner_id": str(hold.get("owner_id") or ""),
                    "lock_until": hold.get("lock_until").isoformat() if hold.get("lock_until") else "",
                }
            return {
                "acquired": True,
                "lock_token": str(row.get("lock_token") or ""),
                "owner_id": str(row.get("owner_id") or owner),
                "lock_until": row.get("lock_until").isoformat() if row.get("lock_until") else "",
            }

    def release_config_publish_lock(
        self,
        *,
        scope_type: str,
        scope_id: str,
        lock_token: str,
    ) -> None:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        token = str(lock_token or "").strip()
        if not token:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM config_publish_locks WHERE scope_type=%s AND scope_id=%s AND lock_token=%s",
                (st, sid, token),
            )

    def get_config_publish_idempotency(
        self,
        *,
        scope_type: str,
        scope_id: str,
        idempotency_key: str,
        ttl_seconds: int = 600,
    ) -> dict | None:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        ttl = max(30, min(int(ttl_seconds or 600), 86400))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT request_hash,response_json,created_at
                FROM config_publish_idempotency
                WHERE scope_type=%s AND scope_id=%s AND idempotency_key=%s
                  AND created_at >= NOW() - (%s::text || ' seconds')::interval""",
                (st, sid, key, str(ttl)),
            )
            row = cur.fetchone() or {}
            if not row:
                return None
            return {
                "request_hash": str(row.get("request_hash") or ""),
                "response_json": row.get("response_json") or {},
                "created_at": row.get("created_at").isoformat() if row.get("created_at") else "",
            }

    def save_config_publish_idempotency(
        self,
        *,
        scope_type: str,
        scope_id: str,
        idempotency_key: str,
        request_hash: str,
        response_json: dict,
    ) -> None:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        key = str(idempotency_key or "").strip()
        if not key:
            return
        req_hash = str(request_hash or "").strip()
        if not req_hash:
            req_hash = hashlib.sha256(json.dumps(response_json or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        payload = json.dumps(response_json or {}, ensure_ascii=False)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO config_publish_idempotency (scope_type,scope_id,idempotency_key,request_hash,response_json,created_at)
                VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
                ON CONFLICT (scope_type,scope_id,idempotency_key) DO UPDATE SET
                  request_hash=EXCLUDED.request_hash,
                  response_json=EXCLUDED.response_json,
                  created_at=NOW()""",
                (st, sid, key, req_hash, payload),
            )

    def list_config_audit_logs(
        self,
        *,
        scope_type: str,
        scope_id: str,
        limit: int = 200,
        config_key: str = "",
        actor_id: str = "",
        action: str = "",
        release_id: int = 0,
        trace_id: str = "",
        from_ts: str = "",
        to_ts: str = "",
    ) -> list[dict]:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        cap = max(1, min(int(limit or 200), 2000))
        where = ["scope_type=%s", "scope_id=%s"]
        params: list = [st, sid]
        if str(config_key or "").strip():
            where.append("config_key=%s")
            params.append(str(config_key).strip())
        if str(actor_id or "").strip():
            where.append("actor_id=%s")
            params.append(str(actor_id).strip())
        if str(action or "").strip():
            where.append("action=%s")
            params.append(str(action).strip().upper())
        if int(release_id or 0) > 0:
            where.append("release_id=%s")
            params.append(int(release_id))
        if str(trace_id or "").strip():
            where.append("trace_id=%s")
            params.append(str(trace_id).strip())
        if str(from_ts or "").strip():
            where.append("created_at >= %s::timestamptz")
            params.append(str(from_ts).strip())
        if str(to_ts or "").strip():
            where.append("created_at <= %s::timestamptz")
            params.append(str(to_ts).strip())

        sql_text = f"""
            SELECT audit_id,action,scope_type,scope_id,release_id,config_key,old_json,new_json,actor_id,trace_id,metadata_json,created_at
            FROM config_audit_logs
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT %s
        """
        params.append(cap)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql_text, tuple(params))
            rows = []
            for row in (cur.fetchall() or []):
                item = dict(row)
                item["created_at"] = row["created_at"].isoformat() if row.get("created_at") else ""
                rows.append(item)
            return rows

    def delete_config_release(self, *, scope_type: str, scope_id: str, release_id: int) -> bool:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """DELETE FROM config_releases
                WHERE release_id=%s AND scope_type=%s AND scope_id=%s""",
                (int(release_id), st, sid),
            )
            return int(cur.rowcount or 0) > 0

    def switch_config_to_default(
        self,
        *,
        scope_type: str,
        scope_id: str,
        actor_id: str,
        trace_id: str = "",
    ) -> dict:
        self._ensure_indexes()
        st, sid = self._normalize_config_scope(scope_type, scope_id)
        defaults = self._load_defaults_from_schema()
        restored = 0
        with self._conn() as conn, conn.cursor() as cur:
            for key, val in defaults.items():
                payload = json.dumps(val, ensure_ascii=False)
                cur.execute(
                    """INSERT INTO config_values (config_key,scope_type,scope_id,value_json,version,updated_by,updated_at)
                    VALUES (%s,%s,%s,%s::jsonb,1,%s,NOW())
                    ON CONFLICT (config_key,scope_type,scope_id) DO UPDATE SET
                        value_json=EXCLUDED.value_json, version=config_values.version + 1,
                        updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                    (str(key), st, sid, payload, actor_id or "system"),
                )
                restored += 1
            cur.execute(
                """INSERT INTO config_audit_logs
                (action,scope_type,scope_id,config_key,old_json,new_json,actor_id,trace_id,created_at)
                VALUES ('SWITCH_DEFAULT',%s,%s,'*','null'::jsonb,%s::jsonb,%s,%s,NOW())""",
                (
                    st,
                    sid,
                    json.dumps({"restored": restored}, ensure_ascii=False),
                    actor_id or "system",
                    trace_id or "",
                ),
            )
        return {"status": "ok", "scope_type": st, "scope_id": sid, "restored": restored, "mode": "switch_default"}

    def get_config_release_diff(self, *, release_from: int, release_to: int) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT release_id,changes_json,snapshot_json FROM config_releases WHERE release_id=%s", (int(release_from),))
            a = cur.fetchone() or {}
            cur.execute("SELECT release_id,changes_json,snapshot_json FROM config_releases WHERE release_id=%s", (int(release_to),))
            b = cur.fetchone() or {}
        snap_a = a.get("snapshot_json") if isinstance(a.get("snapshot_json"), dict) else {}
        snap_b = b.get("snapshot_json") if isinstance(b.get("snapshot_json"), dict) else {}
        if snap_a:
            map_a = {str(k): v for k, v in snap_a.items() if str(k or "").strip()}
        else:
            map_a = {str(it.get("key")): it.get("value") for it in (a.get("changes_json") or []) if str(it.get("key") or "").strip()}
        if snap_b:
            map_b = {str(k): v for k, v in snap_b.items() if str(k or "").strip()}
        else:
            map_b = {str(it.get("key")): it.get("value") for it in (b.get("changes_json") or []) if str(it.get("key") or "").strip()}
        keys = sorted(set(map_a.keys()) | set(map_b.keys()))
        changes = []
        for key in keys:
            va = map_a.get(key, None)
            vb = map_b.get(key, None)
            if va != vb:
                changes.append({"key": key, "from": va, "to": vb})
        return {
            "from_release_id": int(release_from),
            "to_release_id": int(release_to),
            "changes": changes,
            "change_count": len(changes),
        }


kb_store = KnowledgeBaseStore()
