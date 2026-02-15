from __future__ import annotations

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
    def __init__(self) -> None:
        self.dsn = _postgres_dsn()
        self.collection_prefix = os.getenv("QDRANT_COLLECTION_PREFIX", "apf")
        self.qdrant = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=(os.getenv("QDRANT_API_KEY", "") or None),
            timeout=30.0,
        )
        self._initialized = False

    def _conn(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _collection_name(self, tenant_id: str, kb_id: str) -> str:
        raw = f"{self.collection_prefix}_{tenant_id}_{kb_id}".lower()
        return re.sub(r"[^a-z0-9_]", "_", raw)

    def _strip_nul(self, value: str | None) -> str:
        return (value or "").replace("\x00", "")

    def _ensure_indexes(self) -> None:
        if self._initialized:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_bases (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, name TEXT NOT NULL,
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
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS frozen BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_dirty BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ NULL")
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
        if next_status not in {"OPEN", "ACK", "RESOLVED"}:
            raise ValueError("status must be OPEN|ACK|RESOLVED")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE api_usage_alerts SET status=%s WHERE tenant_id=%s AND alert_id=%s",
                (next_status, self._strip_nul(tenant_id), int(alert_id)),
            )
            return bool(cur.rowcount and int(cur.rowcount) > 0)

    def ensure_kb(self, tenant_id: str, kb_id: str, name: str | None = None) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id, kb_id, name, summary, status, stats, created_at, updated_at)
                VALUES (%s, %s, %s, '', 'ACTIVE', '{}'::jsonb, NOW(), NOW())
                ON CONFLICT (tenant_id, kb_id) DO NOTHING""",
                (tenant_id, kb_id, name or kb_id),
            )

    def get_kb(self, tenant_id: str, kb_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,summary,frozen,summary_embedding_json,summary_dirty,summary_updated_at,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_kbs(self, tenant_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,summary,frozen,summary_embedding_json,summary_dirty,summary_updated_at,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s ORDER BY created_at ASC",
                (tenant_id,),
            )
            return [dict(row) for row in cur.fetchall()]

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
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id,kb_id,name,summary,status,summary_dirty,summary_updated_at,stats,created_at,updated_at)
                VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s,'{}'::jsonb,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id) DO UPDATE SET
                    name=EXCLUDED.name, summary=EXCLUDED.summary,
                    summary_dirty=EXCLUDED.summary_dirty,
                    summary_updated_at=EXCLUDED.summary_updated_at,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    final_name,
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

    def delete_kb(self, tenant_id: str, kb_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM task_events WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM tasks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            deleted = cur.rowcount > 0
        collection_name = self._collection_name(tenant_id, kb_id)
        if self.qdrant.collection_exists(collection_name=collection_name):
            self.qdrant.delete_collection(collection_name=collection_name)
        return deleted

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
        chunk_overlap = int(chunk_overlap or 50)
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
                    int(chunk_overlap or 50),
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
        self.ensure_kb(tenant_id, to_kb_id)
        doc = self.get_document(tenant_id, from_kb_id, doc_id)
        if not doc:
            return False
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_id,chunk_index,text,frozen,embedding_json,metadata
                FROM chunks
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s
                ORDER BY chunk_index ASC""",
                (tenant_id, from_kb_id, doc_id),
            )
            chunks = [dict(r) for r in cur.fetchall()]
        drafts = self.list_chunk_drafts(tenant_id, from_kb_id, doc_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (
                    tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,tags_json,
                    summary_overview,summary_text,summary_embedding_json,summary_dirty,text_size_bytes,word_count,
                    status,chunk_count,embedded_count,embedding_dirty,frozen,
                    chunk_method,chunk_size,chunk_overlap,chunk_delimiter,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
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
                    self._strip_nul(doc.get("status", "UPLOADED")),
                    int(doc.get("chunk_count") or 0),
                    int(doc.get("embedded_count") or 0),
                    bool(doc.get("embedding_dirty", True)),
                    bool(doc.get("frozen", False)),
                    self._strip_nul(doc.get("chunk_method", "sentence")),
                    int(doc.get("chunk_size") or 500),
                    int(doc.get("chunk_overlap") or 50),
                    self._strip_nul(doc.get("chunk_delimiter", "")),
                ),
            )
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, to_kb_id, doc_id))
            for ch in chunks:
                cur.execute(
                    """INSERT INTO chunks (tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,NOW(),NOW())""",
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
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, from_kb_id, doc_id))
        # Rebuild vectors in destination collection using stored embeddings and remove source points.
        records: list[ChunkRecord] = []
        for ch in chunks:
            embedding = ch.get("embedding_json") or []
            if not isinstance(embedding, list) or not embedding:
                continue
            records.append(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=to_kb_id,
                    doc_id=doc_id,
                    chunk_id=ch["chunk_id"],
                    chunk_index=int(ch.get("chunk_index") or 0),
                    text=self._strip_nul(ch.get("text", "")),
                    embedding=[float(x) for x in embedding],
                    metadata=ch.get("metadata") or {},
                )
            )
        self.replace_chunks(tenant_id, to_kb_id, doc_id, records)
        from_collection = self._collection_name(tenant_id, from_kb_id)
        if self.qdrant.collection_exists(collection_name=from_collection):
            self.qdrant.delete(
                collection_name=from_collection,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=from_kb_id)),
                            qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
            )
        self.recompute_kb_stats(tenant_id, from_kb_id)
        self.recompute_kb_stats(tenant_id, to_kb_id)
        return True

    def get_document(self, tenant_id: str, kb_id: str, doc_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,summary_overview,summary_text,summary_embedding_json,summary_dirty,text_size_bytes,word_count,
                tags_json,frozen,
                status,chunk_count,embedded_count,embedding_dirty,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at
                FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (tenant_id, kb_id, doc_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

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
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            for idx, text in enumerate(chunk_texts):
                safe_text = self._strip_nul(text)
                raw = f"{tenant_id}:{kb_id}:{doc_id}:{idx}:{safe_text}"
                chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
                cur.execute(
                    """INSERT INTO chunks (tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s::jsonb,NOW(),NOW())""",
                    (
                        tenant_id,
                        kb_id,
                        doc_id,
                        chunk_id,
                        idx,
                        safe_text,
                        "[]",
                        json.dumps({"filename": safe_filename}, ensure_ascii=False),
                    ),
                )
        self._update_document_counters(tenant_id, kb_id, doc_id, len(chunk_texts), 0, True)
        self.recompute_kb_stats(tenant_id, kb_id)

    def _ensure_collection(self, collection_name: str, vector_size: int) -> None:
        if self.qdrant.collection_exists(collection_name=collection_name):
            current_size = self._get_collection_vector_size(collection_name)
            if current_size is None or current_size == int(vector_size):
                return
            # Auto-migrate collection dimension when embedding model changes.
            self.qdrant.delete_collection(collection_name=collection_name)
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

    def replace_chunks(self, tenant_id: str, kb_id: str, doc_id: str, chunks: list[ChunkRecord]) -> None:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            for c in chunks:
                safe_text = self._strip_nul(c.text)
                safe_filename = self._strip_nul((c.metadata or {}).get("filename", "unknown"))
                cur.execute(
                    """INSERT INTO chunks (tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,frozen,embedding_json,metadata,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s::jsonb,NOW(),NOW())""",
                    (
                        c.tenant_id,
                        c.kb_id,
                        c.doc_id,
                        c.chunk_id,
                        c.chunk_index,
                        safe_text,
                        json.dumps(c.embedding, ensure_ascii=False),
                        json.dumps({"filename": safe_filename}, ensure_ascii=False),
                    ),
                )
        if chunks:
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
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),
                        vector=c.embedding,
                        payload={
                            "tenant_id": c.tenant_id,
                            "kb_id": c.kb_id,
                            "doc_id": c.doc_id,
                            "chunk_id": c.chunk_id,
                            "chunk_index": c.chunk_index,
                            "text": self._strip_nul(c.text),
                            "source": self._strip_nul((c.metadata or {}).get("filename", "unknown")),
                        },
                    )
                    for c in chunks
                ],
            )
        self._update_document_counters(tenant_id, kb_id, doc_id, len(chunks), len(chunks), False)
        self.recompute_kb_stats(tenant_id, kb_id)

    def search_chunks(self, tenant_id: str, kb_id: str, query_vector: list[float], top_k: int, doc_ids: list[str] | None = None) -> list[dict]:
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
        collection_name = self._collection_name(tenant_id, kb_id)
        conditions = [
            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
        ]
        if doc_ids:
            conditions.append(qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=doc_ids)))
        results = self.qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=qmodels.Filter(must=conditions),
            limit=max(1, min(int(top_k), 50)),
            with_payload=True,
        )
        items = [
            {
                "doc_id": (hit.payload or {}).get("doc_id", ""),
                "chunk_id": (hit.payload or {}).get("chunk_id", ""),
                "chunk_index": (hit.payload or {}).get("chunk_index", 0),
                "text": (hit.payload or {}).get("text", ""),
                "score": float(hit.score),
                "source": (hit.payload or {}).get("source", "unknown"),
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
                """SELECT doc_id,frozen
                FROM documents
                WHERE tenant_id=%s AND kb_id=%s AND doc_id = ANY(%s)""",
                (tenant_id, kb_id, hit_doc_ids),
            )
            doc_rows = [dict(r) for r in cur.fetchall()]
            doc_frozen_map = {str(r.get("doc_id") or ""): bool(r.get("frozen")) for r in doc_rows}

            cur.execute(
                """SELECT doc_id,chunk_id,frozen
                FROM chunks
                WHERE tenant_id=%s AND kb_id=%s AND doc_id = ANY(%s)""",
                (tenant_id, kb_id, hit_doc_ids),
            )
            chunk_rows = [dict(r) for r in cur.fetchall()]
            chunk_frozen_map = {
                (str(r.get("doc_id") or ""), str(r.get("chunk_id") or "")): bool(r.get("frozen"))
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
            if (doc_id, chunk_id) not in chunk_frozen_map:
                continue
            if doc_frozen_map.get(doc_id, False):
                continue
            if chunk_frozen_map.get((doc_id, chunk_id), False):
                continue
            filtered.append(item)
        return filtered

    def list_documents(self, tenant_id: str, kb_id: str, limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 500))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,status,chunk_count,embedded_count,embedding_dirty,
                tags_json,frozen,
                summary_overview,summary_text,summary_dirty,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at,text_size_bytes,word_count
                FROM documents WHERE tenant_id=%s AND kb_id=%s ORDER BY updated_at DESC LIMIT %s""",
                (tenant_id, kb_id, cap),
            )
            docs = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT doc_id,COUNT(*)::int AS cnt FROM chunks WHERE tenant_id=%s AND kb_id=%s GROUP BY doc_id",
                (tenant_id, kb_id),
            )
            counts = {row["doc_id"]: int(row["cnt"]) for row in cur.fetchall()}
        for doc in docs:
            actual = counts.get(doc["doc_id"], 0)
            status = str(doc.get("status") or "").upper()
            # During active pipeline stages, keep preview counters from documents table.
            # Chunks table may still hold previous generation until indexing replacement finishes.
            if status in {"PARSING", "CHUNKING", "EMBEDDING", "INDEXING", "UPLOADING"}:
                continue
            if int(doc.get("chunk_count") or 0) != actual or int(doc.get("embedded_count") or 0) != actual:
                self._update_document_counters(tenant_id, kb_id, doc["doc_id"], actual, actual, False)
                doc["chunk_count"] = actual
                doc["embedded_count"] = actual
                doc["embedding_dirty"] = False
        return docs

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

    def build_document_text(self, tenant_id: str, kb_id: str, doc_id: str) -> str:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT text FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC", (tenant_id, kb_id, doc_id))
            parts = [str(row["text"] or "") for row in cur.fetchall()]
        return "\n".join([p for p in parts if p])

    def delete_document(self, tenant_id: str, kb_id: str, doc_id: str) -> bool:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            deleted = cur.rowcount > 0
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
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
        self.recompute_kb_stats(tenant_id, kb_id)
        return deleted

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
    ) -> None:
        self._ensure_indexes()
        safe_filename = self._strip_nul(filename)
        safe_batch_id = self._strip_nul(batch_id)
        safe_batch_name = self._strip_nul(batch_name)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (task_id,tenant_id,kb_id,doc_id,task_type,status,stage,filename,batch_id,batch_name,batch_is_parent,progress_current,progress_total,progress_pct,cancel_requested,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s,0,0,0,FALSE,NOW(),NOW())
                ON CONFLICT (task_id) DO UPDATE SET
                    tenant_id=EXCLUDED.tenant_id,kb_id=EXCLUDED.kb_id,doc_id=EXCLUDED.doc_id,
                    task_type=EXCLUDED.task_type,status='PENDING',stage=EXCLUDED.stage,
                    filename=COALESCE(NULLIF(EXCLUDED.filename, ''), tasks.filename),
                    batch_id=COALESCE(NULLIF(EXCLUDED.batch_id, ''), tasks.batch_id),
                    batch_name=COALESCE(NULLIF(EXCLUDED.batch_name, ''), tasks.batch_name),
                    batch_is_parent=EXCLUDED.batch_is_parent,
                    progress_current=0,progress_total=0,progress_pct=0,
                    cancel_requested=FALSE,updated_at=NOW()""",
                (
                    task_id,
                    tenant_id,
                    kb_id,
                    doc_id,
                    task_type,
                    stage,
                    safe_filename,
                    safe_batch_id,
                    safe_batch_name,
                    bool(batch_is_parent),
                ),
            )
        self.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_CREATED",
            stage,
            {"task_type": task_type, "batch_id": safe_batch_id, "batch_name": safe_batch_name, "batch_is_parent": bool(batch_is_parent)},
        )
        if safe_batch_id and not batch_is_parent:
            self.refresh_task_batch(safe_batch_id, tenant_id=tenant_id, kb_id=kb_id)

    def update_task_record(self, task_id: str, status: str, stage: str, error_message: str | None = None) -> None:
        self._ensure_indexes()
        batch_row = None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET status=%s, stage=%s, error_message=%s,
                    progress_current=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') THEN progress_total ELSE progress_current END,
                    progress_pct=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') AND progress_total > 0 THEN 100 ELSE progress_pct END,
                    updated_at=NOW()
                WHERE task_id=%s""",
                (status, stage, error_message, status, status, task_id),
            )
            cur.execute("SELECT tenant_id,kb_id,batch_id,batch_is_parent FROM tasks WHERE task_id=%s", (task_id,))
            batch_row = cur.fetchone()
        if str(status or "").upper() in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            self.release_task_dedup_locks(task_id)
        self.append_task_event(task_id, "", "", "", "TASK_STATUS", stage, {"status": status, "error_message": error_message})
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
                SET stage=%s, progress_current=%s, progress_total=%s, progress_pct=%s, updated_at=NOW()
                WHERE task_id=%s""",
                (stage, c, t, pct, task_id),
            )
            cur.execute("SELECT tenant_id,kb_id,batch_id,batch_is_parent FROM tasks WHERE task_id=%s", (task_id,))
            batch_row = cur.fetchone()
        if batch_row and batch_row.get("batch_id") and not bool(batch_row.get("batch_is_parent")):
            self.refresh_task_batch(str(batch_row["batch_id"]), tenant_id=str(batch_row["tenant_id"]), kb_id=str(batch_row["kb_id"]))

    def request_cancel_task(self, task_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET cancel_requested=TRUE,status='CANCELLING',updated_at=NOW()
                WHERE task_id=%s AND status IN ('PENDING','RUNNING','CANCELLING')""",
                (task_id,),
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
                    cancel_requested,error_message,tasks.created_at,tasks.updated_at
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
                    cancel_requested,error_message,tasks.created_at,tasks.updated_at
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
                total_items,succeeded_items,failed_items,running_items,status,created_by,created_at,updated_at
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
                cancel_requested,error_message,tasks.created_at,tasks.updated_at
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
                    COUNT(*) FILTER (WHERE status IN ('PENDING','RUNNING','CANCELLING'))::int AS running_items
                FROM tasks
                WHERE tenant_id=%s AND kb_id=%s AND batch_id=%s AND batch_is_parent=FALSE""",
                (tenant_id, kb_id, batch_id),
            )
            agg = cur.fetchone() or {"total_items": 0, "succeeded_items": 0, "failed_items": 0, "running_items": 0}
            total_items = int(agg.get("total_items") or 0)
            succeeded_items = int(agg.get("succeeded_items") or 0)
            failed_items = int(agg.get("failed_items") or 0)
            running_items = int(agg.get("running_items") or 0)
            completed = succeeded_items + failed_items
            progress_pct = int((completed * 100 / total_items)) if total_items > 0 else 0
            if running_items > 0:
                status = "RUNNING"
                stage = "BATCH_RUNNING"
            elif failed_items > 0:
                status = "FAILED"
                stage = "BATCH_FAILED"
            else:
                status = "SUCCEEDED"
                stage = "BATCH_COMPLETED"
            cur.execute(
                """UPDATE task_batches
                SET total_items=%s,succeeded_items=%s,failed_items=%s,running_items=%s,status=%s,updated_at=NOW()
                WHERE batch_id=%s AND tenant_id=%s
                RETURNING parent_task_id""",
                (total_items, succeeded_items, failed_items, running_items, status, batch_id, tenant_id),
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
                        f"{failed_items} 项失败" if failed_items > 0 else None,
                        parent_task_id,
                    ),
                )
        return {
            "batch_id": batch_id,
            "total_items": total_items,
            "succeeded_items": succeeded_items,
            "failed_items": failed_items,
            "running_items": running_items,
            "status": status,
            "progress_pct": progress_pct,
        }

    def get_task(self, task_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                tasks.batch_id,tasks.batch_name,tasks.batch_is_parent,
                progress_current,progress_total,progress_pct,
                cancel_requested,error_message,tasks.created_at,tasks.updated_at
                FROM tasks
                LEFT JOIN documents
                ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                WHERE tasks.task_id=%s""",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def append_task_event(self, task_id: str, tenant_id: str, kb_id: str, doc_id: str, event_type: str, stage: str, payload: dict) -> None:
        self._ensure_indexes()
        if not tenant_id or not kb_id or not doc_id:
            rec = self.get_task(task_id) or {}
            tenant_id = tenant_id or rec.get("tenant_id", "")
            kb_id = kb_id or rec.get("kb_id", "")
            doc_id = doc_id or rec.get("doc_id", "")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO task_events (task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())""",
                (task_id, tenant_id, kb_id, doc_id, event_type, stage, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def list_task_events(self, task_id: str, limit: int = 200) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                FROM (
                    SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                    FROM task_events
                    WHERE task_id=%s
                    ORDER BY ts DESC
                    LIMIT %s
                ) t
                ORDER BY ts ASC""",
                (task_id, cap),
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

    def commit_chunk_drafts(self, tenant_id: str, kb_id: str, doc_id: str, embed_fn) -> dict:
        self._ensure_indexes()
        chunks = self.list_chunks(tenant_id, kb_id, doc_id, limit=10000)
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
            max_file_size_mb = int(value.get("max_file_size_mb", 20))
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


kb_store = KnowledgeBaseStore()
