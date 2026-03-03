from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import psycopg
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

from .config import get_setting
from .models import ChatMessage, ChatRunState, ChatSession


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, ChatSession] = {}
        self._messages: Dict[str, List[ChatMessage]] = {}
        self._session_memory: Dict[str, Dict[str, Any]] = {}
        self._user_profile_memory: Dict[str, Dict[str, Any]] = {}
        self._session_memory_items: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_rerank_jobs: Dict[str, Dict[str, Any]] = {}
        self._runs: Dict[str, ChatRunState] = {}
        self._cancel_flags: Dict[str, bool] = {}
        self._persist_backend = self._resolve_store_backend()
        self._postgres_dsn = self._resolve_postgres_dsn()
        self._pg_conn = None
        self._pg_schema_ready = False
        self._pg_schema_last_try_at = 0.0
        self._pg_schema_retry_seconds = 5.0
        self._store_path = self._resolve_store_path()
        self._disk_flush_interval_ms = self._resolve_disk_flush_interval_ms()
        self._disk_dirty = False
        self._disk_flush_timer: Optional[threading.Timer] = None
        self._last_disk_save_at = 0.0
        self._last_disk_save_monotonic = 0.0
        self._last_pg_save_at = 0.0
        self._disk_write_count = 0
        self._pg_write_count = 0
        self._last_recent_activity_cleanup_at = 0.0
        self._last_recent_activity_cleanup_deleted = 0
        self._load_from_persistent()

    def _resolve_store_backend(self) -> str:
        raw = str(get_setting("chat_history_store_backend", "postgres_prefer")).strip().lower()
        if raw in {"postgres", "postgres_only"}:
            return "postgres_only"
        if raw in {"postgres_prefer", "postgres+file", "hybrid"}:
            return "postgres_prefer"
        if raw in {"file", "file_only", "json"}:
            return "file_only"
        return "postgres_prefer"

    def _resolve_postgres_dsn(self) -> str:
        candidates = [
            str(get_setting("chat_history_postgres_dsn", "")).strip(),
            str(os.getenv("CHAT_HISTORY_POSTGRES_DSN", "")).strip(),
            str(os.getenv("POSTGRES_DSN", "")).strip(),
            str(get_setting("postgres_dsn", "")).strip(),
        ]
        for dsn in candidates:
            if dsn:
                return dsn
        return ""

    def _resolve_store_path(self) -> Path:
        configured = str(
            get_setting("chat_history_store_path", "/app/data/chat-plane/chat_history_store.json")
        ).strip()
        if not configured:
            configured = "/app/data/chat-plane/chat_history_store.json"
        return Path(configured)

    def _resolve_disk_flush_interval_ms(self) -> int:
        try:
            value = int(get_setting("chat_history_disk_flush_interval_ms", 800))
        except Exception:
            value = 800
        return max(0, value)

    def _load_from_persistent(self) -> None:
        # Prefer database in postgres modes, fallback to local file.
        if self._persist_backend in {"postgres_only", "postgres_prefer"}:
            if self._load_from_postgres():
                return
            if self._persist_backend == "postgres_only":
                self._clear_memory_locked()
                return
        if self._load_from_disk():
            # One-time migration path: if file fallback loaded under postgres_prefer, sync to DB.
            if self._persist_backend == "postgres_prefer":
                with self._lock:
                    self._save_to_postgres_locked(self._payload_locked())
            return
        self._clear_memory_locked()

    def _clear_memory_locked(self) -> None:
        with self._lock:
            self._sessions = {}
            self._messages = {}
            self._session_memory = {}
            self._user_profile_memory = {}
            self._session_memory_items = {}
            self._memory_rerank_jobs = {}

    def _payload_locked(self) -> Dict[str, Any]:
        return {
            "sessions": {k: v.model_dump() for k, v in self._sessions.items()},
            "messages": {k: [m.model_dump() for m in items] for k, items in self._messages.items()},
            "session_memory": self._session_memory,
            "user_profile_memory": self._user_profile_memory,
            "session_memory_items": self._session_memory_items,
            "memory_rerank_jobs": self._memory_rerank_jobs,
        }

    def _load_from_payload(self, payload: Dict[str, Any]) -> bool:
        try:
            sessions = payload.get("sessions", {})
            messages = payload.get("messages", {})
            session_memory = payload.get("session_memory", {})
            user_profile_memory = payload.get("user_profile_memory", {})
            session_memory_items = payload.get("session_memory_items", {})
            memory_rerank_jobs = payload.get("memory_rerank_jobs", {})

            loaded_sessions: Dict[str, ChatSession] = {}
            loaded_messages: Dict[str, List[ChatMessage]] = {}

            if isinstance(sessions, dict):
                for sid, raw in sessions.items():
                    try:
                        loaded_sessions[str(sid)] = ChatSession(**raw)
                    except Exception:
                        continue
            if isinstance(messages, dict):
                for sid, raw_items in messages.items():
                    if not isinstance(raw_items, list):
                        continue
                    parsed: List[ChatMessage] = []
                    for raw in raw_items:
                        try:
                            parsed.append(ChatMessage(**raw))
                        except Exception:
                            continue
                    loaded_messages[str(sid)] = parsed

            with self._lock:
                self._sessions = loaded_sessions
                self._messages = loaded_messages
                self._session_memory = session_memory if isinstance(session_memory, dict) else {}
                self._user_profile_memory = user_profile_memory if isinstance(user_profile_memory, dict) else {}
                self._session_memory_items = (
                    session_memory_items if isinstance(session_memory_items, dict) else {}
                )
                self._memory_rerank_jobs = (
                    memory_rerank_jobs if isinstance(memory_rerank_jobs, dict) else {}
                )
            return True
        except Exception:
            return False

    def _load_from_disk(self) -> bool:
        try:
            if not self._store_path.exists():
                return False
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            return self._load_from_payload(payload)
        except Exception:
            return False

    def _ensure_pg_table(self) -> bool:
        if psycopg is None or not self._postgres_dsn:
            return False
        now = time.time()
        if self._pg_schema_ready:
            return True
        if now - self._pg_schema_last_try_at < self._pg_schema_retry_seconds:
            return False
        self._pg_schema_last_try_at = now
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_history_store_snapshots (
                      snapshot_id SMALLINT PRIMARY KEY,
                      payload JSONB NOT NULL,
                      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                      session_id TEXT PRIMARY KEY,
                      tenant_id TEXT NOT NULL,
                      actor_id TEXT NOT NULL,
                      title TEXT NOT NULL,
                      kb_id TEXT NULL,
                      created_at TIMESTAMPTZ NOT NULL,
                      updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_session_title_meta (
                      session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                      attempt_count INTEGER NOT NULL DEFAULT 0,
                      is_stable BOOLEAN NOT NULL DEFAULT FALSE,
                      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                      message_id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                      role TEXT NOT NULL,
                      content TEXT NOT NULL,
                      created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_session_memory (
                      session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                      short_summary TEXT NOT NULL DEFAULT '',
                      long_summary TEXT NOT NULL DEFAULT '',
                      updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_user_profiles (
                      tenant_id TEXT NOT NULL,
                      actor_id TEXT NOT NULL,
                      profile_summary TEXT NOT NULL DEFAULT '',
                      last_profile_message_at TIMESTAMPTZ NULL,
                      updated_at TIMESTAMPTZ NOT NULL,
                      PRIMARY KEY (tenant_id, actor_id)
                    )
                    """
                )
                cur.execute("ALTER TABLE chat_user_profiles ADD COLUMN IF NOT EXISTS last_profile_message_at TIMESTAMPTZ NULL")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_memory_items (
                      item_id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                      text TEXT NOT NULL,
                      importance DOUBLE PRECISION NOT NULL,
                      kind TEXT NOT NULL,
                      created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'conversation'")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS ttl_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS importance_v2 DOUBLE PRECISION NULL")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS conflict_state TEXT NOT NULL DEFAULT 'active'")
                cur.execute("ALTER TABLE chat_memory_items ADD COLUMN IF NOT EXISTS shadowed_by_item_id TEXT NULL")
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_actor_updated ON chat_sessions(tenant_id, actor_id, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_title_meta_updated ON chat_session_title_meta(updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at ASC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_created ON chat_memory_items(session_id, created_at ASC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_importance ON chat_memory_items(session_id, importance DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_ttl ON chat_memory_items(session_id, ttl_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_items_session_conflict ON chat_memory_items(session_id, conflict_state, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session_current_updated ON chat_session_summaries(session_id, is_current, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_tenant_actor_updated ON chat_session_summaries(tenant_id, actor_id, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_user_recent_activities_tenant_actor_created ON chat_user_recent_activities(tenant_id, actor_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_user_recent_activities_ttl ON chat_user_recent_activities(ttl_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_fusion_logs_tenant_actor_created ON chat_memory_fusion_logs(tenant_id, actor_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_fusion_logs_session_created ON chat_memory_fusion_logs(session_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_segments_tenant_actor_updated ON chat_session_segments(tenant_id, actor_id, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_segments_session_updated ON chat_session_segments(session_id, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session_segments_keywords_gin ON chat_session_segments USING GIN (keywords)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_runs_tenant_actor_updated ON chat_runs(tenant_id, actor_id, updated_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_rerank_jobs_status_created ON chat_memory_rerank_jobs(status, created_at ASC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_rerank_jobs_session_updated ON chat_memory_rerank_jobs(session_id, updated_at DESC)")
            self._pg_schema_ready = True
            return True
        except Exception:
            self._pg_schema_ready = False
            self._pg_conn = None
            return False

    def _pg_get_conn_locked(self):
        if psycopg is None or not self._postgres_dsn:
            return None
        conn = self._pg_conn
        if conn is not None:
            try:
                if not getattr(conn, "closed", False):
                    return conn
            except Exception:
                pass
            self._pg_conn = None
        try:
            self._pg_conn = psycopg.connect(self._postgres_dsn, autocommit=True)
            return self._pg_conn
        except Exception:
            self._pg_conn = None
            return None

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        raw = str(value or "").strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)

    def _load_from_postgres_normalized(self) -> bool:
        try:
            with psycopg.connect(self._postgres_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT session_id, tenant_id, actor_id, title, kb_id, created_at, updated_at FROM chat_sessions"
                    )
                    rows = cur.fetchall() or []
                    if not rows:
                        return False
                    loaded_sessions: Dict[str, ChatSession] = {}
                    loaded_messages: Dict[str, List[ChatMessage]] = {}
                    for session_id, tenant_id, actor_id, title, kb_id, created_at, updated_at in rows:
                        sid = str(session_id)
                        loaded_sessions[sid] = ChatSession(
                            session_id=sid,
                            tenant_id=str(tenant_id),
                            actor_id=str(actor_id),
                            title=str(title or ""),
                            kb_id=(str(kb_id) if kb_id else None),
                            created_at=created_at.isoformat(),
                            updated_at=updated_at.isoformat(),
                        )
                        loaded_messages[sid] = []

                    cur.execute(
                        "SELECT message_id, session_id, role, content, created_at FROM chat_messages ORDER BY created_at ASC"
                    )
                    for message_id, session_id, role, content, created_at in cur.fetchall() or []:
                        sid = str(session_id)
                        if sid not in loaded_sessions:
                            continue
                        loaded_messages.setdefault(sid, []).append(
                            ChatMessage(
                                message_id=str(message_id),
                                session_id=sid,
                                role=str(role),  # type: ignore[arg-type]
                                content=str(content or ""),
                                created_at=created_at.isoformat(),
                            )
                        )

                    loaded_session_memory: Dict[str, Dict[str, Any]] = {}
                    cur.execute(
                        "SELECT session_id, short_summary, long_summary, updated_at FROM chat_session_memory"
                    )
                    for session_id, short_summary, long_summary, updated_at in cur.fetchall() or []:
                        sid = str(session_id)
                        loaded_session_memory[sid] = {
                            "short_summary": str(short_summary or ""),
                            "long_summary": str(long_summary or ""),
                            "updated_at": updated_at.isoformat(),
                        }

                    loaded_user_profiles: Dict[str, Dict[str, Any]] = {}
                    cur.execute(
                        "SELECT tenant_id, actor_id, profile_summary, last_profile_message_at, updated_at FROM chat_user_profiles"
                    )
                    for tenant_id, actor_id, profile_summary, last_profile_message_at, updated_at in cur.fetchall() or []:
                        key = self._user_key(str(tenant_id), str(actor_id))
                        loaded_user_profiles[key] = {
                            "profile_summary": str(profile_summary or ""),
                            "last_profile_message_at": (
                                last_profile_message_at.isoformat() if last_profile_message_at else None
                            ),
                            "updated_at": updated_at.isoformat(),
                        }

                    loaded_memory_items: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in loaded_sessions}
                    cur.execute(
                        """
                        SELECT item_id, session_id, text, importance, importance_v2, confidence, kind, created_at, conflict_state, shadowed_by_item_id
                        FROM chat_memory_items
                        ORDER BY created_at ASC
                        """
                    )
                    for item_id, session_id, text, importance, importance_v2, confidence, kind, created_at, conflict_state, shadowed_by_item_id in cur.fetchall() or []:
                        sid = str(session_id)
                        if sid not in loaded_sessions:
                            continue
                        loaded_memory_items.setdefault(sid, []).append(
                            {
                                "item_id": str(item_id),
                                "text": str(text or ""),
                                "importance": float(importance or 0.0),
                                "importance_v2": (float(importance_v2) if importance_v2 is not None else None),
                                "confidence": float(confidence or 0.5),
                                "kind": str(kind or "episodic"),
                                "conflict_state": str(conflict_state or "active"),
                                "shadowed_by_item_id": (str(shadowed_by_item_id) if shadowed_by_item_id else None),
                                "created_at": created_at.isoformat(),
                            }
                        )

                    loaded_runs: Dict[str, ChatRunState] = {}
                    cur.execute(
                        """
                        SELECT run_id, session_id, tenant_id, actor_id, mode, status, stage,
                               progress_current, progress_total, error_message, started_at, updated_at, completed_at
                        FROM chat_runs
                        ORDER BY updated_at DESC
                        LIMIT 2000
                        """
                    )
                    for (
                        run_id,
                        session_id,
                        tenant_id,
                        actor_id,
                        mode,
                        status,
                        stage,
                        progress_current,
                        progress_total,
                        error_message,
                        started_at,
                        updated_at,
                        completed_at,
                    ) in cur.fetchall() or []:
                        loaded_runs[str(run_id)] = ChatRunState(
                            run_id=str(run_id),
                            session_id=str(session_id),
                            tenant_id=str(tenant_id),
                            actor_id=str(actor_id),
                            mode=str(mode),
                            status=str(status),  # type: ignore[arg-type]
                            stage=str(stage),
                            progress_current=int(progress_current or 0),
                            progress_total=int(progress_total or 0),
                            error_message=(str(error_message) if error_message else None),
                            started_at=started_at.isoformat(),
                            updated_at=updated_at.isoformat(),
                            completed_at=(completed_at.isoformat() if completed_at else None),
                        )

                    loaded_rerank_jobs: Dict[str, Dict[str, Any]] = {}
                    cur.execute(
                        """
                        SELECT job_id, session_id, tenant_id, actor_id, requested_by, request_limit, status,
                               result_json, error_message, created_at, updated_at, started_at, finished_at
                        FROM chat_memory_rerank_jobs
                        ORDER BY created_at DESC
                        LIMIT 5000
                        """
                    )
                    for row in cur.fetchall() or []:
                        parsed = self._row_to_memory_rerank_job(row)
                        loaded_rerank_jobs[str(parsed.get("job_id") or "")] = parsed

            with self._lock:
                self._sessions = loaded_sessions
                self._messages = loaded_messages
                self._session_memory = loaded_session_memory
                self._user_profile_memory = loaded_user_profiles
                self._session_memory_items = loaded_memory_items
                self._memory_rerank_jobs = loaded_rerank_jobs
                self._runs = loaded_runs
                self._cancel_flags = {rid: False for rid in loaded_runs}
            return True
        except Exception:
            return False

    def _load_from_postgres(self) -> bool:
        if not self._ensure_pg_table():
            return False
        if self._load_from_postgres_normalized():
            return True
        try:
            with psycopg.connect(self._postgres_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM chat_history_store_snapshots WHERE snapshot_id = 1"
                    )
                    row = cur.fetchone()
            if not row:
                return False
            payload = row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                return False
            return self._load_from_payload(payload)
        except Exception:
            return False

    def _sync_normalized_tables_locked(self, conn, payload: Dict[str, Any]) -> None:
        sessions = payload.get("sessions") or {}
        messages = payload.get("messages") or {}
        session_memory = payload.get("session_memory") or {}
        user_profiles = payload.get("user_profile_memory") or {}
        memory_items = payload.get("session_memory_items") or {}
        memory_rerank_jobs = payload.get("memory_rerank_jobs") or {}
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_memory_rerank_jobs")
            cur.execute("DELETE FROM chat_memory_items")
            cur.execute("DELETE FROM chat_session_memory")
            cur.execute("DELETE FROM chat_messages")
            cur.execute("DELETE FROM chat_sessions")
            cur.execute("DELETE FROM chat_user_profiles")

            for sid, raw in sessions.items():
                if not isinstance(raw, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO chat_sessions(session_id, tenant_id, actor_id, title, kb_id, created_at, updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(raw.get("session_id") or sid),
                        str(raw.get("tenant_id") or ""),
                        str(raw.get("actor_id") or ""),
                        str(raw.get("title") or ""),
                        raw.get("kb_id"),
                        self._parse_iso(str(raw.get("created_at") or utc_now_iso())),
                        self._parse_iso(str(raw.get("updated_at") or utc_now_iso())),
                    ),
                )

            for sid, arr in messages.items():
                if not isinstance(arr, list):
                    continue
                for raw in arr:
                    if not isinstance(raw, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO chat_messages(message_id, session_id, role, content, created_at)
                        VALUES(%s,%s,%s,%s,%s)
                        """,
                        (
                            str(raw.get("message_id") or str(uuid.uuid4())),
                            str(raw.get("session_id") or sid),
                            str(raw.get("role") or "user"),
                            str(raw.get("content") or ""),
                            self._parse_iso(str(raw.get("created_at") or utc_now_iso())),
                        ),
                    )

            for sid, raw in session_memory.items():
                if not isinstance(raw, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO chat_session_memory(session_id, short_summary, long_summary, updated_at)
                    VALUES(%s,%s,%s,%s)
                    """,
                    (
                        str(sid),
                        str(raw.get("short_summary") or ""),
                        str(raw.get("long_summary") or ""),
                        self._parse_iso(str(raw.get("updated_at") or utc_now_iso())),
                    ),
                )

            for key, raw in user_profiles.items():
                if not isinstance(raw, dict):
                    continue
                tenant_id, actor_id = str(key).split(":", 1) if ":" in str(key) else ("", "")
                if not tenant_id or not actor_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO chat_user_profiles(tenant_id, actor_id, profile_summary, last_profile_message_at, updated_at)
                    VALUES(%s,%s,%s,%s,%s)
                    """,
                    (
                        tenant_id,
                        actor_id,
                        str(raw.get("profile_summary") or ""),
                        self._parse_iso(str(raw.get("last_profile_message_at") or ""))
                        if str(raw.get("last_profile_message_at") or "").strip()
                        else None,
                        self._parse_iso(str(raw.get("updated_at") or utc_now_iso())),
                    ),
                )

            for sid, arr in memory_items.items():
                if not isinstance(arr, list):
                    continue
                for raw in arr:
                    if not isinstance(raw, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO chat_memory_items(
                          item_id, session_id, text, importance, importance_v2, confidence, kind, created_at, conflict_state, shadowed_by_item_id
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            str(raw.get("item_id") or str(uuid.uuid4())),
                            str(sid),
                            str(raw.get("text") or ""),
                            float(raw.get("importance") or 0.0),
                            (
                                float(raw.get("importance_v2"))
                                if raw.get("importance_v2") is not None
                                else None
                            ),
                            float(raw.get("confidence") or 0.5),
                            str(raw.get("kind") or "episodic"),
                            self._parse_iso(str(raw.get("created_at") or utc_now_iso())),
                            str(raw.get("conflict_state") or "active"),
                            (
                                str(raw.get("shadowed_by_item_id") or "").strip() or None
                            ),
                        ),
                    )

            if isinstance(memory_rerank_jobs, dict):
                for _, raw in memory_rerank_jobs.items():
                    if not isinstance(raw, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO chat_memory_rerank_jobs(
                          job_id, session_id, tenant_id, actor_id, requested_by, request_limit, status,
                          result_json, error_message, created_at, updated_at, started_at, finished_at
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                        ON CONFLICT(job_id) DO NOTHING
                        """,
                        (
                            str(raw.get("job_id") or str(uuid.uuid4())),
                            str(raw.get("session_id") or ""),
                            str(raw.get("tenant_id") or ""),
                            str(raw.get("actor_id") or ""),
                            str(raw.get("requested_by") or ""),
                            int(raw.get("request_limit") or 200),
                            str(raw.get("status") or "PENDING"),
                            json.dumps(raw.get("result"), ensure_ascii=False)
                            if isinstance(raw.get("result"), dict)
                            else None,
                            str(raw.get("error_message") or "") or None,
                            self._parse_iso(str(raw.get("created_at") or utc_now_iso())),
                            self._parse_iso(str(raw.get("updated_at") or utc_now_iso())),
                            self._parse_iso(str(raw.get("started_at") or ""))
                            if str(raw.get("started_at") or "").strip()
                            else None,
                            self._parse_iso(str(raw.get("finished_at") or ""))
                            if str(raw.get("finished_at") or "").strip()
                            else None,
                        ),
                    )

    def _save_to_postgres_locked(self, payload: Dict[str, Any]) -> bool:
        if not self._ensure_pg_table():
            return False
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return False
            self._sync_normalized_tables_locked(conn, payload)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_history_store_snapshots (snapshot_id, payload, updated_at)
                    VALUES (1, %s::jsonb, NOW())
                    ON CONFLICT (snapshot_id)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                    """,
                    [json.dumps(payload, ensure_ascii=False)],
                )
            self._pg_write_count += 1
            self._last_pg_save_at = time.time()
            return True
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return False

    def _pg_enabled_locked(self) -> bool:
        if self._persist_backend == "file_only":
            return False
        return self._ensure_pg_table()

    def _mark_pg_write_locked(self) -> None:
        self._pg_write_count += 1
        self._last_pg_save_at = time.time()

    def _pg_upsert_session_locked(self, session: ChatSession) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_sessions(session_id, tenant_id, actor_id, title, kb_id, created_at, updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(session_id) DO UPDATE SET
                      tenant_id=EXCLUDED.tenant_id,
                      actor_id=EXCLUDED.actor_id,
                      title=EXCLUDED.title,
                      kb_id=EXCLUDED.kb_id,
                      updated_at=EXCLUDED.updated_at
                    """,
                    (
                        session.session_id,
                        session.tenant_id,
                        session.actor_id,
                        session.title,
                        session.kb_id,
                        self._parse_iso(session.created_at),
                        self._parse_iso(session.updated_at),
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_delete_session_locked(self, session_id: str) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_sessions WHERE session_id=%s", (session_id,))
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_insert_message_locked(self, message: ChatMessage) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_messages(message_id, session_id, role, content, created_at)
                    VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(message_id) DO NOTHING
                    """,
                    (
                        message.message_id,
                        message.session_id,
                        message.role,
                        message.content,
                        self._parse_iso(message.created_at),
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_upsert_session_memory_locked(self, session_id: str, item: Dict[str, Any]) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_session_memory(session_id, short_summary, long_summary, updated_at)
                    VALUES(%s,%s,%s,%s)
                    ON CONFLICT(session_id) DO UPDATE SET
                      short_summary=EXCLUDED.short_summary,
                      long_summary=EXCLUDED.long_summary,
                      updated_at=EXCLUDED.updated_at
                    """,
                    (
                        session_id,
                        str(item.get("short_summary") or ""),
                        str(item.get("long_summary") or ""),
                        self._parse_iso(str(item.get("updated_at") or utc_now_iso())),
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_upsert_user_profile_locked(self, tenant_id: str, actor_id: str, item: Dict[str, Any]) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_user_profiles(tenant_id, actor_id, profile_summary, last_profile_message_at, updated_at)
                    VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id, actor_id) DO UPDATE SET
                      profile_summary=EXCLUDED.profile_summary,
                      last_profile_message_at=EXCLUDED.last_profile_message_at,
                      updated_at=EXCLUDED.updated_at
                    """,
                    (
                        tenant_id,
                        actor_id,
                        str(item.get("profile_summary") or ""),
                        self._parse_iso(str(item.get("last_profile_message_at") or ""))
                        if str(item.get("last_profile_message_at") or "").strip()
                        else None,
                        self._parse_iso(str(item.get("updated_at") or utc_now_iso())),
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_insert_memory_item_locked(self, session_id: str, item: Dict[str, Any]) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_memory_items(
                      item_id, session_id, text, importance, importance_v2, confidence, kind, created_at, conflict_state, shadowed_by_item_id
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(item_id) DO NOTHING
                    """,
                    (
                        str(item.get("item_id") or str(uuid.uuid4())),
                        session_id,
                        str(item.get("text") or ""),
                        float(item.get("importance") or 0.0),
                        (
                            float(item.get("importance_v2"))
                            if item.get("importance_v2") is not None
                            else None
                        ),
                        float(item.get("confidence") or 0.5),
                        str(item.get("kind") or "episodic"),
                        self._parse_iso(str(item.get("created_at") or utc_now_iso())),
                        str(item.get("conflict_state") or "active"),
                        (
                            str(item.get("shadowed_by_item_id") or "").strip() or None
                        ),
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_replace_memory_items_locked(self, session_id: str, items: List[Dict[str, Any]]) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_memory_items WHERE session_id=%s", (session_id,))
                for item in items:
                    cur.execute(
                        """
                        INSERT INTO chat_memory_items(
                          item_id, session_id, text, importance, importance_v2, confidence, kind, created_at, conflict_state, shadowed_by_item_id
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(item_id) DO NOTHING
                        """,
                        (
                            str(item.get("item_id") or str(uuid.uuid4())),
                            session_id,
                            str(item.get("text") or ""),
                            float(item.get("importance") or 0.0),
                            (
                                float(item.get("importance_v2"))
                                if item.get("importance_v2") is not None
                                else None
                            ),
                            float(item.get("confidence") or 0.5),
                            str(item.get("kind") or "episodic"),
                            self._parse_iso(str(item.get("created_at") or utc_now_iso())),
                            str(item.get("conflict_state") or "active"),
                            (
                                str(item.get("shadowed_by_item_id") or "").strip() or None
                            ),
                        ),
                    )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _pg_insert_memory_rerank_job_locked(self, job: Dict[str, Any]) -> None:
        if not self._pg_enabled_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_memory_rerank_jobs(
                      job_id, session_id, tenant_id, actor_id, requested_by, request_limit,
                      status, result_json, error_message, created_at, updated_at, started_at, finished_at
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                    ON CONFLICT(job_id) DO NOTHING
                    """,
                    (
                        str(job.get("job_id") or str(uuid.uuid4())),
                        str(job.get("session_id") or ""),
                        str(job.get("tenant_id") or ""),
                        str(job.get("actor_id") or ""),
                        str(job.get("requested_by") or ""),
                        int(job.get("request_limit") or 200),
                        str(job.get("status") or "PENDING"),
                        json.dumps(job.get("result"), ensure_ascii=False)
                        if isinstance(job.get("result"), dict)
                        else None,
                        str(job.get("error_message") or "") or None,
                        self._parse_iso(str(job.get("created_at") or utc_now_iso())),
                        self._parse_iso(str(job.get("updated_at") or utc_now_iso())),
                        self._parse_iso(str(job.get("started_at") or ""))
                        if str(job.get("started_at") or "").strip()
                        else None,
                        self._parse_iso(str(job.get("finished_at") or ""))
                        if str(job.get("finished_at") or "").strip()
                        else None,
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False

    def _pg_update_memory_rerank_job_locked(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        if not self._pg_enabled_locked():
            return
        jid = str(job_id or "").strip()
        if not jid:
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            fields: List[str] = []
            params: List[Any] = []
            if status is not None:
                fields.append("status=%s")
                params.append(str(status))
            if result is not None:
                fields.append("result_json=%s::jsonb")
                params.append(json.dumps(result, ensure_ascii=False))
            if error_message is not None:
                fields.append("error_message=%s")
                params.append(str(error_message)[:2000] if error_message else None)
            if started_at is not None:
                fields.append("started_at=%s")
                params.append(self._parse_iso(str(started_at)) if str(started_at).strip() else None)
            if finished_at is not None:
                fields.append("finished_at=%s")
                params.append(self._parse_iso(str(finished_at)) if str(finished_at).strip() else None)
            fields.append("updated_at=%s")
            params.append(self._parse_iso(str(updated_at or utc_now_iso())))
            params.append(jid)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE chat_memory_rerank_jobs SET {', '.join(fields)} WHERE job_id=%s",
                    tuple(params),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False

    def _pg_claim_memory_rerank_job_locked(self) -> Optional[Dict[str, Any]]:
        if not self._pg_enabled_locked():
            return None
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH one AS (
                      SELECT job_id
                      FROM chat_memory_rerank_jobs
                      WHERE status='PENDING'
                      ORDER BY created_at ASC
                      LIMIT 1
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE chat_memory_rerank_jobs t
                    SET status='RUNNING', started_at=NOW(), updated_at=NOW()
                    FROM one
                    WHERE t.job_id=one.job_id
                    RETURNING t.job_id, t.session_id, t.tenant_id, t.actor_id, t.requested_by, t.request_limit, t.status,
                              t.result_json, t.error_message, t.created_at, t.updated_at, t.started_at, t.finished_at
                    """
                )
                row = cur.fetchone()
            if not row:
                return None
            self._mark_pg_write_locked()
            return self._row_to_memory_rerank_job(row)
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return None

    def _row_to_memory_rerank_job(self, row: Any) -> Dict[str, Any]:
        (
            job_id,
            session_id,
            tenant_id,
            actor_id,
            requested_by,
            request_limit,
            status,
            result_json,
            error_message,
            created_at,
            updated_at,
            started_at,
            finished_at,
        ) = row
        parsed_result = None
        if isinstance(result_json, dict):
            parsed_result = dict(result_json)
        return {
            "job_id": str(job_id),
            "session_id": str(session_id),
            "tenant_id": str(tenant_id),
            "actor_id": str(actor_id),
            "requested_by": str(requested_by or ""),
            "request_limit": int(request_limit or 200),
            "status": str(status or "PENDING"),
            "result": parsed_result,
            "error_message": str(error_message or "") or None,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
        }

    def _pg_get_memory_rerank_job_locked(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not self._pg_enabled_locked():
            return None
        jid = str(job_id or "").strip()
        if not jid:
            return None
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, session_id, tenant_id, actor_id, requested_by, request_limit, status,
                           result_json, error_message, created_at, updated_at, started_at, finished_at
                    FROM chat_memory_rerank_jobs
                    WHERE job_id=%s
                    LIMIT 1
                    """,
                    (jid,),
                )
                row = cur.fetchone()
            if not row:
                return None
            return self._row_to_memory_rerank_job(row)
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return None

    def _pg_find_pending_rerank_job_by_session_locked(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not self._pg_enabled_locked():
            return None
        sid = str(session_id or "").strip()
        if not sid:
            return None
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, session_id, tenant_id, actor_id, requested_by, request_limit, status,
                           result_json, error_message, created_at, updated_at, started_at, finished_at
                    FROM chat_memory_rerank_jobs
                    WHERE session_id=%s AND status IN ('PENDING', 'RUNNING')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (sid,),
                )
                row = cur.fetchone()
            if not row:
                return None
            return self._row_to_memory_rerank_job(row)
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return None

    def _pg_list_memory_rerank_jobs_locked(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._pg_enabled_locked():
            return []
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, session_id, tenant_id, actor_id, requested_by, request_limit, status,
                           result_json, error_message, created_at, updated_at, started_at, finished_at
                    FROM chat_memory_rerank_jobs
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (max(1, min(500, int(limit))),),
                )
                rows = cur.fetchall() or []
            return [self._row_to_memory_rerank_job(r) for r in rows]
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return []

    def _upsert_run_to_postgres(self, run: ChatRunState) -> None:
        if self._persist_backend == "file_only":
            return
        if not self._ensure_pg_table():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_runs(
                      run_id, session_id, tenant_id, actor_id, mode, status, stage,
                      progress_current, progress_total, error_message, started_at, updated_at, completed_at
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id) DO UPDATE SET
                      status=EXCLUDED.status,
                      stage=EXCLUDED.stage,
                      progress_current=EXCLUDED.progress_current,
                      progress_total=EXCLUDED.progress_total,
                      error_message=EXCLUDED.error_message,
                      updated_at=EXCLUDED.updated_at,
                      completed_at=EXCLUDED.completed_at
                    """,
                    (
                        run.run_id,
                        run.session_id,
                        run.tenant_id,
                        run.actor_id,
                        run.mode,
                        run.status,
                        run.stage,
                        run.progress_current,
                        run.progress_total,
                        run.error_message,
                        self._parse_iso(run.started_at),
                        self._parse_iso(run.updated_at),
                        self._parse_iso(run.completed_at) if run.completed_at else None,
                    ),
                )
            self._mark_pg_write_locked()
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return

    def _save_to_disk_locked(self, payload: Dict[str, Any]) -> bool:
        try:
            if not self._store_path.parent.exists():
                self._store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store_path.with_suffix(self._store_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(self._store_path))
            self._disk_dirty = False
            self._disk_write_count += 1
            self._last_disk_save_at = time.time()
            self._last_disk_save_monotonic = time.monotonic()
            return True
        except Exception:
            return False

    def _flush_disk_now_locked(self) -> bool:
        if self._disk_flush_timer is not None:
            self._disk_flush_timer = None
        payload = self._payload_locked()
        return self._save_to_disk_locked(payload)

    def _flush_disk_timer_callback(self) -> None:
        with self._lock:
            self._disk_flush_timer = None
            if not self._disk_dirty:
                return
            self._flush_disk_now_locked()

    def _queue_disk_flush_locked(self, force: bool = False) -> None:
        if force or self._disk_flush_interval_ms <= 0:
            self._flush_disk_now_locked()
            return
        self._disk_dirty = True
        if self._disk_flush_timer is not None:
            return
        now = time.monotonic()
        elapsed_ms = max(0, int((now - (self._last_disk_save_monotonic or 0.0)) * 1000))
        remaining_ms = max(0, self._disk_flush_interval_ms - elapsed_ms)
        timer = threading.Timer(remaining_ms / 1000.0, self._flush_disk_timer_callback)
        timer.daemon = True
        self._disk_flush_timer = timer
        timer.start()

    def _save_persistent_locked(self) -> None:
        payload = self._payload_locked()
        if self._persist_backend == "postgres_only":
            self._save_to_postgres_locked(payload)
            return
        if self._persist_backend == "postgres_prefer":
            pg_ok = self._save_to_postgres_locked(payload)
            # Keep local mirror even when postgres is available; helps local inspection.
            self._save_to_disk_locked(payload)
            if not pg_ok:
                return
            return
        self._save_to_disk_locked(payload)

    def force_flush(self) -> Dict[str, Any]:
        with self._lock:
            ok = self._flush_disk_now_locked()
            return {"status": "ok" if ok else "error", "disk_flushed": bool(ok)}

    def get_store_status(self) -> Dict[str, Any]:
        with self._lock:
            sessions = len(self._sessions)
            messages = sum(len(items) for items in self._messages.values())
            runs_total = len(self._runs)
            runs_running = sum(1 for r in self._runs.values() if str(getattr(r, "status", "")).upper() in {"PENDING", "RUNNING", "CANCELLING"})
            return {
                "persist_backend": self._persist_backend,
                "postgres_dsn_configured": bool(self._postgres_dsn),
                "postgres_available": self._ensure_pg_table(),
                "postgres_connection_reused": self._pg_conn is not None and not bool(getattr(self._pg_conn, "closed", False)),
                "store_path": str(self._store_path),
                "disk_flush_interval_ms": self._disk_flush_interval_ms,
                "disk_dirty": self._disk_dirty,
                "disk_flush_scheduled": self._disk_flush_timer is not None,
                "disk_write_count": self._disk_write_count,
                "pg_write_count": self._pg_write_count,
                "last_disk_save_at": (
                    datetime.fromtimestamp(self._last_disk_save_at, tz=timezone.utc).isoformat()
                    if self._last_disk_save_at > 0
                    else None
                ),
                "last_pg_save_at": (
                    datetime.fromtimestamp(self._last_pg_save_at, tz=timezone.utc).isoformat()
                    if self._last_pg_save_at > 0
                    else None
                ),
                "counts": {
                    "sessions": sessions,
                    "messages": messages,
                    "session_memory": len(self._session_memory),
                    "user_profile_memory": len(self._user_profile_memory),
                    "memory_items": sum(len(items) for items in self._session_memory_items.values()),
                    "memory_rerank_jobs": len(self._memory_rerank_jobs),
                    "runs_total": runs_total,
                    "runs_running_like": runs_running,
                },
                "recent_activity_cleanup": {
                    "last_at": (
                        datetime.fromtimestamp(self._last_recent_activity_cleanup_at, tz=timezone.utc).isoformat()
                        if self._last_recent_activity_cleanup_at > 0
                        else None
                    ),
                    "last_deleted": self._last_recent_activity_cleanup_deleted,
                },
            }

    def add_recent_activity(
        self,
        tenant_id: str,
        actor_id: str,
        session_id: Optional[str],
        activity_type: str,
        activity_text: str,
        importance: float = 0.5,
        ttl_seconds: Optional[int] = None,
    ) -> Optional[str]:
        if self._persist_backend == "file_only":
            return None
        with self._lock:
            if not self._ensure_pg_table():
                return None
            now_iso = utc_now_iso()
            activity_id = str(uuid.uuid4())
            ttl_at = None
            if ttl_seconds and ttl_seconds > 0:
                ttl_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_user_recent_activities(
                          activity_id, tenant_id, actor_id, session_id, activity_type, activity_text, importance, ttl_at, created_at, updated_at
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            activity_id,
                            tenant_id,
                            actor_id,
                            session_id,
                            str(activity_type or "query"),
                            str(activity_text or ""),
                            float(max(0.0, min(1.0, importance))),
                            datetime.fromtimestamp(ttl_at, tz=timezone.utc) if ttl_at else None,
                            self._parse_iso(now_iso),
                            self._parse_iso(now_iso),
                        ),
                    )
                self._mark_pg_write_locked()
                return activity_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def upsert_recent_profile_activity(
        self,
        tenant_id: str,
        actor_id: str,
        session_id: Optional[str],
        activity_text: str,
        *,
        importance: float = 0.75,
        ttl_seconds: Optional[int] = None,
        min_interval_seconds: int = 1800,
    ) -> Optional[str]:
        """Upsert cross-session profile activity signal.

        Semantics: recent-activity is a profile-layer summary signal, not raw per-turn log.
        """
        if self._persist_backend == "file_only":
            return None
        text = str(activity_text or "").strip()
        if not text:
            return None
        with self._lock:
            if not self._ensure_pg_table():
                return None
            now_iso = utc_now_iso()
            ttl_at = None
            if ttl_seconds and ttl_seconds > 0:
                ttl_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT activity_id, activity_text, created_at
                        FROM chat_user_recent_activities
                        WHERE tenant_id=%s AND actor_id=%s AND activity_type='profile_signal'
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, actor_id),
                    )
                    row = cur.fetchone()
                    if row:
                        latest_id = str(row[0])
                        latest_text = str(row[1] or "")
                        latest_created_at = row[2]
                        can_merge = False
                        try:
                            age_seconds = (
                                datetime.now(timezone.utc) - latest_created_at.astimezone(timezone.utc)
                            ).total_seconds()
                            can_merge = age_seconds < max(1, int(min_interval_seconds))
                        except Exception:
                            can_merge = False
                        if can_merge or latest_text == text:
                            cur.execute(
                                """
                                UPDATE chat_user_recent_activities
                                SET session_id=%s,
                                    activity_text=%s,
                                    importance=%s,
                                    ttl_at=%s,
                                    updated_at=%s
                                WHERE activity_id=%s
                                """,
                                (
                                    session_id,
                                    text,
                                    float(max(0.0, min(1.0, importance))),
                                    datetime.fromtimestamp(ttl_at, tz=timezone.utc) if ttl_at else None,
                                    self._parse_iso(now_iso),
                                    latest_id,
                                ),
                            )
                            self._mark_pg_write_locked()
                            return latest_id
                    activity_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO chat_user_recent_activities(
                          activity_id, tenant_id, actor_id, session_id, activity_type, activity_text, importance, ttl_at, created_at, updated_at
                        )
                        VALUES(%s,%s,%s,%s,'profile_signal',%s,%s,%s,%s,%s)
                        """,
                        (
                            activity_id,
                            tenant_id,
                            actor_id,
                            session_id,
                            text,
                            float(max(0.0, min(1.0, importance))),
                            datetime.fromtimestamp(ttl_at, tz=timezone.utc) if ttl_at else None,
                            self._parse_iso(now_iso),
                            self._parse_iso(now_iso),
                        ),
                    )
                self._mark_pg_write_locked()
                return activity_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def upsert_recent_activity_signal(
        self,
        tenant_id: str,
        actor_id: str,
        session_id: Optional[str],
        activity_type: str,
        activity_text: str,
        *,
        importance: float = 0.65,
        ttl_seconds: Optional[int] = None,
        min_interval_seconds: int = 900,
    ) -> Optional[str]:
        if self._persist_backend == "file_only":
            return None
        a_type = str(activity_type or "").strip()
        text = str(activity_text or "").strip()
        if not a_type or not text:
            return None
        with self._lock:
            if not self._ensure_pg_table():
                return None
            now_iso = utc_now_iso()
            ttl_at = None
            if ttl_seconds and ttl_seconds > 0:
                ttl_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT activity_id, activity_text, created_at
                        FROM chat_user_recent_activities
                        WHERE tenant_id=%s AND actor_id=%s AND activity_type=%s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, actor_id, a_type),
                    )
                    row = cur.fetchone()
                    if row:
                        latest_id = str(row[0])
                        latest_text = str(row[1] or "")
                        latest_created_at = row[2]
                        can_merge = False
                        try:
                            age_seconds = (
                                datetime.now(timezone.utc) - latest_created_at.astimezone(timezone.utc)
                            ).total_seconds()
                            can_merge = age_seconds < max(1, int(min_interval_seconds))
                        except Exception:
                            can_merge = False
                        if can_merge or latest_text == text:
                            cur.execute(
                                """
                                UPDATE chat_user_recent_activities
                                SET session_id=%s,
                                    activity_text=%s,
                                    importance=%s,
                                    ttl_at=%s,
                                    updated_at=%s
                                WHERE activity_id=%s
                                """,
                                (
                                    session_id,
                                    text,
                                    float(max(0.0, min(1.0, importance))),
                                    datetime.fromtimestamp(ttl_at, tz=timezone.utc) if ttl_at else None,
                                    self._parse_iso(now_iso),
                                    latest_id,
                                ),
                            )
                            self._mark_pg_write_locked()
                            return latest_id
                    activity_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO chat_user_recent_activities(
                          activity_id, tenant_id, actor_id, session_id, activity_type, activity_text, importance, ttl_at, created_at, updated_at
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            activity_id,
                            tenant_id,
                            actor_id,
                            session_id,
                            a_type,
                            text,
                            float(max(0.0, min(1.0, importance))),
                            datetime.fromtimestamp(ttl_at, tz=timezone.utc) if ttl_at else None,
                            self._parse_iso(now_iso),
                            self._parse_iso(now_iso),
                        ),
                    )
                self._mark_pg_write_locked()
                return activity_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def list_recent_activities(
        self,
        tenant_id: str,
        actor_id: str,
        activity_types: Optional[List[str]] = None,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        if self._persist_backend == "file_only":
            return []
        types = [str(x).strip() for x in (activity_types or []) if str(x).strip()]
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    if types:
                        cur.execute(
                            """
                            SELECT activity_id, session_id, activity_type, activity_text, importance, created_at, updated_at
                            FROM chat_user_recent_activities
                            WHERE tenant_id=%s
                              AND actor_id=%s
                              AND activity_type = ANY(%s)
                              AND (ttl_at IS NULL OR ttl_at > NOW())
                            ORDER BY created_at DESC
                            LIMIT %s
                            """,
                            (tenant_id, actor_id, types, max(1, int(limit))),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT activity_id, session_id, activity_type, activity_text, importance, created_at, updated_at
                            FROM chat_user_recent_activities
                            WHERE tenant_id=%s
                              AND actor_id=%s
                              AND (ttl_at IS NULL OR ttl_at > NOW())
                            ORDER BY created_at DESC
                            LIMIT %s
                            """,
                            (tenant_id, actor_id, max(1, int(limit))),
                        )
                    rows = cur.fetchall() or []
                out: List[Dict[str, Any]] = []
                for activity_id, session_id, activity_type, activity_text, importance, created_at, updated_at in rows:
                    out.append(
                        {
                            "activity_id": str(activity_id),
                            "session_id": str(session_id) if session_id else None,
                            "activity_type": str(activity_type or ""),
                            "activity_text": str(activity_text or ""),
                            "importance": float(importance or 0.0),
                            "created_at": created_at.isoformat() if created_at else None,
                            "updated_at": updated_at.isoformat() if updated_at else None,
                        }
                    )
                return out
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def list_recent_profile_activities(self, tenant_id: str, actor_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        if self._persist_backend == "file_only":
            return []
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT activity_id, session_id, activity_text, importance, created_at, updated_at
                        FROM chat_user_recent_activities
                        WHERE tenant_id=%s
                          AND actor_id=%s
                          AND activity_type='profile_signal'
                          AND (ttl_at IS NULL OR ttl_at > NOW())
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (tenant_id, actor_id, max(1, int(limit))),
                    )
                    rows = cur.fetchall() or []
                out: List[Dict[str, Any]] = []
                for activity_id, session_id, activity_text, importance, created_at, updated_at in rows:
                    out.append(
                        {
                            "activity_id": str(activity_id),
                            "session_id": str(session_id) if session_id else None,
                            "activity_text": str(activity_text or ""),
                            "importance": float(importance or 0.0),
                            "created_at": created_at.isoformat() if created_at else None,
                            "updated_at": updated_at.isoformat() if updated_at else None,
                        }
                    )
                return out
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def list_recent_conversation_samples(
        self,
        tenant_id: str,
        actor_id: str,
        *,
        limit_messages: int = 80,
        max_chars: int = 12000,
        since_created_at: Optional[str] = None,
        roles: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        role_set = {str(x).strip() for x in (roles or []) if str(x).strip()}
        since_iso = str(since_created_at or "").strip()
        since_dt = self._parse_iso(since_iso) if since_iso else None
        if self._persist_backend == "file_only":
            with self._lock:
                sessions = [
                    s.session_id
                    for s in self._sessions.values()
                    if s.tenant_id == tenant_id and s.actor_id == actor_id
                ]
                rows: List[ChatMessage] = []
                for sid in sessions:
                    rows.extend(self._messages.get(sid, []))
            rows.sort(key=lambda x: x.created_at, reverse=True)
            used = 0
            for msg in rows[: max(1, int(limit_messages))]:
                if role_set and str(msg.role) not in role_set:
                    continue
                if since_dt is not None:
                    try:
                        msg_dt = self._parse_iso(str(msg.created_at or ""))
                        if msg_dt is not None and msg_dt <= since_dt:
                            continue
                    except Exception:
                        pass
                content = str(msg.content or "").strip()
                if not content:
                    continue
                line = content[:600]
                if used + len(line) > max_chars:
                    break
                used += len(line)
                out.append(
                    {
                        "session_id": msg.session_id,
                        "role": msg.role,
                        "content": line,
                        "created_at": msg.created_at,
                    }
                )
            out.reverse()
            return out
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    if since_dt is not None:
                        if role_set:
                            cur.execute(
                                """
                                SELECT m.session_id, m.role, m.content, m.created_at
                                FROM chat_messages m
                                INNER JOIN chat_sessions s ON s.session_id = m.session_id
                                WHERE s.tenant_id=%s AND s.actor_id=%s
                                  AND m.created_at > %s
                                  AND m.role = ANY(%s)
                                ORDER BY m.created_at DESC
                                LIMIT %s
                                """,
                                (
                                    tenant_id,
                                    actor_id,
                                    since_dt,
                                    list(role_set),
                                    max(1, int(limit_messages)),
                                ),
                            )
                        else:
                            cur.execute(
                                """
                                SELECT m.session_id, m.role, m.content, m.created_at
                                FROM chat_messages m
                                INNER JOIN chat_sessions s ON s.session_id = m.session_id
                                WHERE s.tenant_id=%s AND s.actor_id=%s
                                  AND m.created_at > %s
                                ORDER BY m.created_at DESC
                                LIMIT %s
                                """,
                                (tenant_id, actor_id, since_dt, max(1, int(limit_messages))),
                            )
                    elif role_set:
                        cur.execute(
                            """
                            SELECT m.session_id, m.role, m.content, m.created_at
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                              AND m.role = ANY(%s)
                            ORDER BY m.created_at DESC
                            LIMIT %s
                            """,
                            (tenant_id, actor_id, list(role_set), max(1, int(limit_messages))),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT m.session_id, m.role, m.content, m.created_at
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                            ORDER BY m.created_at DESC
                            LIMIT %s
                            """,
                            (tenant_id, actor_id, max(1, int(limit_messages))),
                        )
                    rows = cur.fetchall() or []
                used = 0
                for session_id, role, content, created_at in rows:
                    text = str(content or "").strip()
                    if not text:
                        continue
                    line = text[:600]
                    if used + len(line) > max_chars:
                        break
                    used += len(line)
                    out.append(
                        {
                            "session_id": str(session_id),
                            "role": str(role or ""),
                            "content": line,
                            "created_at": created_at.isoformat() if created_at else None,
                        }
                    )
                out.reverse()
                return out
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def count_messages_since(
        self,
        tenant_id: str,
        actor_id: str,
        *,
        since_created_at: Optional[str] = None,
        role: Optional[str] = None,
    ) -> int:
        since_iso = str(since_created_at or "").strip()
        since_dt = self._parse_iso(since_iso) if since_iso else None
        target_role = str(role or "").strip()
        if self._persist_backend == "file_only":
            with self._lock:
                sessions = {
                    s.session_id
                    for s in self._sessions.values()
                    if s.tenant_id == tenant_id and s.actor_id == actor_id
                }
                count = 0
                for sid in sessions:
                    for msg in self._messages.get(sid, []):
                        if target_role and str(msg.role) != target_role:
                            continue
                        if since_dt is not None:
                            md = self._parse_iso(str(msg.created_at or ""))
                            if md is not None and md <= since_dt:
                                continue
                        count += 1
                return count
        with self._lock:
            if not self._ensure_pg_table():
                return 0
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return 0
                with conn.cursor() as cur:
                    if since_dt is not None and target_role:
                        cur.execute(
                            """
                            SELECT COUNT(1)
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                              AND m.created_at > %s
                              AND m.role=%s
                            """,
                            (tenant_id, actor_id, since_dt, target_role),
                        )
                    elif since_dt is not None:
                        cur.execute(
                            """
                            SELECT COUNT(1)
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                              AND m.created_at > %s
                            """,
                            (tenant_id, actor_id, since_dt),
                        )
                    elif target_role:
                        cur.execute(
                            """
                            SELECT COUNT(1)
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                              AND m.role=%s
                            """,
                            (tenant_id, actor_id, target_role),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT COUNT(1)
                            FROM chat_messages m
                            INNER JOIN chat_sessions s ON s.session_id = m.session_id
                            WHERE s.tenant_id=%s AND s.actor_id=%s
                            """,
                            (tenant_id, actor_id),
                        )
                    row = cur.fetchone()
                return int(row[0] or 0) if row else 0
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return 0

    def log_memory_fusion(
        self,
        run_id: str,
        session_id: str,
        tenant_id: str,
        actor_id: str,
        budget_total_tokens: int,
        budget_used_tokens: int,
        layer_stats: Dict[str, Any],
        top_items: List[Dict[str, Any]],
    ) -> Optional[str]:
        if self._persist_backend == "file_only":
            return None
        with self._lock:
            if not self._ensure_pg_table():
                return None
            fusion_id = str(uuid.uuid4())
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_memory_fusion_logs(
                          fusion_id, run_id, session_id, tenant_id, actor_id,
                          budget_total_tokens, budget_used_tokens, layer_stats, top_items, created_at
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                        """,
                        (
                            fusion_id,
                            run_id,
                            session_id,
                            tenant_id,
                            actor_id,
                            int(max(0, budget_total_tokens)),
                            int(max(0, budget_used_tokens)),
                            json.dumps(layer_stats or {}, ensure_ascii=False),
                            json.dumps(top_items or [], ensure_ascii=False),
                            self._parse_iso(utc_now_iso()),
                        ),
                    )
                self._mark_pg_write_locked()
                return fusion_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def upsert_session_segment(
        self,
        tenant_id: str,
        actor_id: str,
        session_id: str,
        source_message_ids: List[str],
        text_content: str,
        token_estimate: int,
        keywords: Optional[List[str]] = None,
    ) -> Optional[str]:
        if self._persist_backend == "file_only":
            return None
        src_ids = [str(x) for x in (source_message_ids or []) if str(x).strip()]
        text = str(text_content or "").strip()
        if not src_ids or not text:
            return None
        segment_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{tenant_id}:{actor_id}:{session_id}:{','.join(src_ids)}",
            )
        )
        kw = [str(x).strip() for x in (keywords or []) if str(x).strip()]
        with self._lock:
            if not self._ensure_pg_table():
                return None
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_session_segments(
                          segment_id, tenant_id, actor_id, session_id, source_message_ids, text_content,
                          token_estimate, keywords, embedding_ref, is_active, created_at, updated_at
                        )
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),NOW())
                        ON CONFLICT(segment_id) DO UPDATE SET
                          text_content=EXCLUDED.text_content,
                          token_estimate=EXCLUDED.token_estimate,
                          keywords=EXCLUDED.keywords,
                          is_active=TRUE,
                          updated_at=NOW()
                        """,
                        (
                            segment_id,
                            tenant_id,
                            actor_id,
                            session_id,
                            src_ids,
                            text,
                            int(max(0, token_estimate)),
                            kw if kw else None,
                            None,
                        ),
                    )
                self._mark_pg_write_locked()
                return segment_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def mark_session_segment_embedding_ref(self, segment_id: str, embedding_ref: str) -> bool:
        if self._persist_backend == "file_only":
            return False
        sid = str(segment_id or "").strip()
        eref = str(embedding_ref or "").strip()
        if not sid or not eref:
            return False
        with self._lock:
            if not self._ensure_pg_table():
                return False
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return False
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE chat_session_segments
                        SET embedding_ref=%s, updated_at=NOW()
                        WHERE segment_id=%s
                        """,
                        (eref, sid),
                    )
                    ok = int(cur.rowcount or 0) > 0
                if ok:
                    self._mark_pg_write_locked()
                return ok
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return False

    @staticmethod
    def _query_terms(text: str) -> List[str]:
        raw = str(text or "").lower()
        terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", raw)
        uniq: List[str] = []
        seen = set()
        for t in terms:
            tt = t.strip()
            if len(tt) < 2:
                continue
            if tt in seen:
                continue
            seen.add(tt)
            uniq.append(tt)
        return uniq[:16]

    def search_session_segments(
        self,
        tenant_id: str,
        actor_id: str,
        query: str,
        limit: int = 4,
        session_id: Optional[str] = None,
        exclude_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._persist_backend == "file_only":
            return []
        terms = self._query_terms(query)
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    if session_id:
                        cur.execute(
                            """
                            SELECT segment_id, session_id, source_message_ids, text_content, token_estimate, keywords, created_at, updated_at
                            FROM chat_session_segments
                            WHERE tenant_id=%s AND actor_id=%s AND is_active=TRUE AND session_id=%s
                            ORDER BY updated_at DESC
                            LIMIT 300
                            """,
                            (tenant_id, actor_id, session_id),
                        )
                    elif exclude_session_id:
                        cur.execute(
                            """
                            SELECT segment_id, session_id, source_message_ids, text_content, token_estimate, keywords, created_at, updated_at
                            FROM chat_session_segments
                            WHERE tenant_id=%s AND actor_id=%s AND is_active=TRUE AND session_id<>%s
                            ORDER BY updated_at DESC
                            LIMIT 300
                            """,
                            (tenant_id, actor_id, exclude_session_id),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT segment_id, session_id, source_message_ids, text_content, token_estimate, keywords, created_at, updated_at
                            FROM chat_session_segments
                            WHERE tenant_id=%s AND actor_id=%s AND is_active=TRUE
                            ORDER BY updated_at DESC
                            LIMIT 300
                            """,
                            (tenant_id, actor_id),
                        )
                    rows = cur.fetchall() or []
                scored: List[tuple[float, Dict[str, Any]]] = []
                total = len(rows)
                for idx, row in enumerate(rows):
                    (
                        segment_id,
                        session_id,
                        source_message_ids,
                        text_content,
                        token_estimate,
                        keywords,
                        created_at,
                        updated_at,
                    ) = row
                    text_l = str(text_content or "").lower()
                    match_count = 0
                    if terms:
                        for t in terms:
                            if t in text_l:
                                match_count += 1
                    elif query and query in text_l:
                        match_count = 1
                    if match_count <= 0:
                        continue
                    recency = 1.0 - (idx / max(total, 1))
                    score = float(match_count) + 0.2 * recency
                    scored.append(
                        (
                            score,
                            {
                                "segment_id": str(segment_id),
                                "session_id": str(session_id),
                                "source_message_ids": list(source_message_ids or []),
                                "text": str(text_content or ""),
                                "text_preview": str(text_content or "")[:220],
                                "token_estimate": int(token_estimate or 0),
                                "keywords": list(keywords or []),
                                "keyword_match_count": int(match_count),
                                "recency_score": round(float(recency), 4),
                                "score": round(score, 4),
                                "created_at": created_at.isoformat() if created_at else None,
                                "updated_at": updated_at.isoformat() if updated_at else None,
                            },
                        )
                    )
                scored.sort(key=lambda x: x[0], reverse=True)
                return [x[1] for x in scored[: max(1, int(limit))]]
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def search_session_segments_by_keywords(
        self,
        tenant_id: str,
        actor_id: str,
        query: str,
        limit: int = 4,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._persist_backend == "file_only":
            return []
        terms = self._query_terms(query)
        if not terms:
            return []
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    if session_id:
                        cur.execute(
                            """
                            SELECT segment_id, session_id, source_message_ids, text_content, token_estimate, keywords, created_at, updated_at
                            FROM chat_session_segments
                            WHERE tenant_id=%s AND actor_id=%s AND is_active=TRUE AND session_id=%s
                            ORDER BY updated_at DESC
                            LIMIT 300
                            """,
                            (tenant_id, actor_id, session_id),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT segment_id, session_id, source_message_ids, text_content, token_estimate, keywords, created_at, updated_at
                            FROM chat_session_segments
                            WHERE tenant_id=%s AND actor_id=%s AND is_active=TRUE
                            ORDER BY updated_at DESC
                            LIMIT 300
                            """,
                            (tenant_id, actor_id),
                        )
                    rows = cur.fetchall() or []
                scored: List[tuple[float, Dict[str, Any]]] = []
                total = len(rows)
                qset = set(terms)
                for idx, row in enumerate(rows):
                    (
                        segment_id,
                        r_session_id,
                        source_message_ids,
                        text_content,
                        token_estimate,
                        keywords,
                        created_at,
                        updated_at,
                    ) = row
                    kws = [str(x).strip().lower() for x in (keywords or []) if str(x).strip()]
                    if not kws:
                        continue
                    overlap = len(qset.intersection(set(kws)))
                    if overlap <= 0:
                        continue
                    recency = 1.0 - (idx / max(total, 1))
                    score = float(overlap) + 0.15 * recency
                    scored.append(
                        (
                            score,
                            {
                                "segment_id": str(segment_id),
                                "session_id": str(r_session_id),
                                "source_message_ids": list(source_message_ids or []),
                                "text": str(text_content or ""),
                                "text_preview": str(text_content or "")[:220],
                                "token_estimate": int(token_estimate or 0),
                                "keywords": list(keywords or []),
                                "keyword_overlap_count": int(overlap),
                                "recency_score": round(float(recency), 4),
                                "score": round(score, 4),
                                "source": "keyword_comp",
                                "created_at": created_at.isoformat() if created_at else None,
                                "updated_at": updated_at.isoformat() if updated_at else None,
                            },
                        )
                    )
                scored.sort(key=lambda x: x[0], reverse=True)
                return [x[1] for x in scored[: max(1, int(limit))]]
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def list_memory_items_for_rerank(self, session_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        sid = str(session_id or "").strip()
        if not sid:
            return []
        if self._persist_backend == "file_only":
            with self._lock:
                items = [
                    dict(x)
                    for x in self._session_memory_items.get(sid, [])
                    if isinstance(x, dict) and str(x.get("conflict_state") or "active") == "active"
                ]
            return items[: max(1, int(limit))]
        with self._lock:
            if not self._ensure_pg_table():
                return []
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return []
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT item_id, text, importance, importance_v2, confidence, kind, created_at, use_count, last_used_at
                        FROM chat_memory_items
                        WHERE session_id=%s AND conflict_state='active'
                        ORDER BY created_at ASC
                        LIMIT %s
                        """,
                        (sid, max(1, int(limit))),
                    )
                    rows = cur.fetchall() or []
                out: List[Dict[str, Any]] = []
                for item_id, text, importance, importance_v2, confidence, kind, created_at, use_count, last_used_at in rows:
                    out.append(
                        {
                            "item_id": str(item_id),
                            "text": str(text or ""),
                            "importance": float(importance or 0.0),
                            "importance_v2": (float(importance_v2) if importance_v2 is not None else None),
                            "confidence": float(confidence or 0.5),
                            "kind": str(kind or "episodic"),
                            "created_at": created_at.isoformat() if created_at else None,
                            "use_count": int(use_count or 0),
                            "last_used_at": last_used_at.isoformat() if last_used_at else None,
                        }
                    )
                return out
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return []

    def update_memory_item_importance_v2(
        self,
        session_id: str,
        item_id: str,
        importance_v2: float,
        confidence: Optional[float] = None,
    ) -> bool:
        sid = str(session_id or "").strip()
        iid = str(item_id or "").strip()
        if not sid or not iid:
            return False
        v2 = float(max(0.0, min(1.0, importance_v2)))
        conf = None if confidence is None else float(max(0.0, min(1.0, confidence)))
        with self._lock:
            # Update in-memory mirror
            arr = self._session_memory_items.get(sid, [])
            for i, one in enumerate(arr):
                if str((one or {}).get("item_id") or "") == iid:
                    nxt = dict(one)
                    nxt["importance_v2"] = v2
                    if conf is not None:
                        nxt["confidence"] = conf
                    arr[i] = nxt
                    break
            if self._persist_backend == "file_only":
                self._queue_disk_flush_locked()
                return True
            if not self._ensure_pg_table():
                return False
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return False
                with conn.cursor() as cur:
                    if conf is None:
                        cur.execute(
                            """
                            UPDATE chat_memory_items
                            SET importance_v2=%s
                            WHERE session_id=%s AND item_id=%s
                            """,
                            (v2, sid, iid),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE chat_memory_items
                            SET importance_v2=%s, confidence=%s
                            WHERE session_id=%s AND item_id=%s
                            """,
                            (v2, conf, sid, iid),
                        )
                    ok = int(cur.rowcount or 0) > 0
                if ok:
                    self._mark_pg_write_locked()
                self._queue_disk_flush_locked()
                return ok
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return False

    def cleanup_expired_recent_activities(self, run_interval_seconds: int = 300) -> Dict[str, int]:
        now = time.time()
        with self._lock:
            if now - self._last_recent_activity_cleanup_at < max(1, int(run_interval_seconds)):
                return {
                    "ran": 0,
                    "deleted": 0,
                    "last_deleted": int(self._last_recent_activity_cleanup_deleted),
                }
            self._last_recent_activity_cleanup_at = now
            if self._persist_backend == "file_only":
                self._last_recent_activity_cleanup_deleted = 0
                return {"ran": 1, "deleted": 0, "last_deleted": 0}
            if not self._ensure_pg_table():
                return {"ran": 0, "deleted": 0, "last_deleted": int(self._last_recent_activity_cleanup_deleted)}
            deleted = 0
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return {"ran": 0, "deleted": 0, "last_deleted": int(self._last_recent_activity_cleanup_deleted)}
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM chat_user_recent_activities WHERE ttl_at IS NOT NULL AND ttl_at <= NOW()"
                    )
                    deleted = int(cur.rowcount or 0)
                self._mark_pg_write_locked()
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                deleted = 0
            self._last_recent_activity_cleanup_deleted = deleted
            return {"ran": 1, "deleted": deleted, "last_deleted": deleted}

    def upsert_session_summary(
        self,
        session_id: str,
        tenant_id: str,
        actor_id: str,
        summary_type: str,
        content: str,
        source_message_start_id: Optional[str] = None,
        source_message_end_id: Optional[str] = None,
    ) -> Optional[int]:
        if self._persist_backend == "file_only":
            return None
        with self._lock:
            if not self._ensure_pg_table():
                return None
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                summary_id: Optional[int] = None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE chat_session_summaries
                        SET is_current = FALSE, updated_at = NOW()
                        WHERE session_id = %s AND summary_type = %s AND is_current = TRUE
                        """,
                        (session_id, summary_type),
                    )
                    cur.execute(
                        """
                        INSERT INTO chat_session_summaries(
                          session_id, tenant_id, actor_id, summary_type, content,
                          source_message_start_id, source_message_end_id, version, is_current, created_at, updated_at
                        )
                        VALUES(
                          %s,%s,%s,%s,%s,%s,%s,
                          COALESCE((SELECT MAX(version) + 1 FROM chat_session_summaries WHERE session_id = %s AND summary_type = %s), 1),
                          TRUE, NOW(), NOW()
                        )
                        RETURNING summary_id
                        """,
                        (
                            session_id,
                            tenant_id,
                            actor_id,
                            summary_type,
                            str(content or ""),
                            source_message_start_id,
                            source_message_end_id,
                            session_id,
                            summary_type,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        try:
                            summary_id = int(row[0])
                        except Exception:
                            summary_id = None
                self._mark_pg_write_locked()
                return summary_id
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def create_session(self, tenant_id: str, actor_id: str, title: str, kb_id: Optional[str]) -> ChatSession:
        now = utc_now_iso()
        session = ChatSession(
            session_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            actor_id=actor_id,
            title=title,
            kb_id=kb_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[session.session_id] = session
            self._messages[session.session_id] = []
            self._session_memory.setdefault(
                session.session_id,
                {
                    "short_summary": "",
                    "long_summary": "",
                    "updated_at": now,
                },
            )
            user_key = self._user_key(tenant_id, actor_id)
            self._user_profile_memory.setdefault(
                user_key,
                {"profile_summary": "", "last_profile_message_at": None, "updated_at": now},
            )
            self._session_memory_items.setdefault(session.session_id, [])
            self._pg_upsert_session_locked(session)
            self._pg_upsert_session_memory_locked(session.session_id, self._session_memory[session.session_id])
            self._pg_upsert_user_profile_locked(
                tenant_id,
                actor_id,
                self._user_profile_memory[user_key],
            )
            self.upsert_session_title_state(session.session_id, attempt_count=0, is_stable=False)
            self._queue_disk_flush_locked()
        return session

    def list_sessions(self, tenant_id: str, actor_id: str, limit: int = 100) -> List[ChatSession]:
        with self._lock:
            items = [
                s
                for s in self._sessions.values()
                if s.tenant_id == tenant_id and s.actor_id == actor_id
            ]
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items[:limit]

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.updated_at = utc_now_iso()
            self._sessions[session_id] = session
            self._pg_upsert_session_locked(session)
            self._queue_disk_flush_locked()

    def rename_session(self, session_id: str, title: str) -> Optional[ChatSession]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            session.title = title
            session.updated_at = utc_now_iso()
            self._sessions[session_id] = session
            self._pg_upsert_session_locked(session)
            state = self.get_session_title_state(session_id)
            self.upsert_session_title_state(
                session_id,
                attempt_count=max(1, int(state.get("attempt_count") or 0)),
                is_stable=True,
            )
            self._queue_disk_flush_locked()
            return session

    def get_session_title_state(self, session_id: str) -> Dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            return {"attempt_count": 0, "is_stable": False}
        if self._persist_backend == "file_only":
            return {"attempt_count": 0, "is_stable": False}
        with self._lock:
            if not self._ensure_pg_table():
                return {"attempt_count": 0, "is_stable": False}
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return {"attempt_count": 0, "is_stable": False}
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT attempt_count, is_stable, updated_at
                        FROM chat_session_title_meta
                        WHERE session_id=%s
                        """,
                        (sid,),
                    )
                    row = cur.fetchone()
                if not row:
                    return {"attempt_count": 0, "is_stable": False}
                attempt_count, is_stable, updated_at = row
                return {
                    "attempt_count": int(attempt_count or 0),
                    "is_stable": bool(is_stable),
                    "updated_at": updated_at.isoformat() if updated_at else None,
                }
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return {"attempt_count": 0, "is_stable": False}

    def upsert_session_title_state(self, session_id: str, attempt_count: int, is_stable: bool) -> bool:
        sid = str(session_id or "").strip()
        if not sid or self._persist_backend == "file_only":
            return False
        with self._lock:
            if not self._ensure_pg_table():
                return False
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return False
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_session_title_meta(session_id, attempt_count, is_stable, updated_at)
                        VALUES(%s,%s,%s,NOW())
                        ON CONFLICT(session_id) DO UPDATE SET
                          attempt_count=EXCLUDED.attempt_count,
                          is_stable=EXCLUDED.is_stable,
                          updated_at=NOW()
                        """,
                        (sid, int(max(0, attempt_count)), bool(is_stable)),
                    )
                self._mark_pg_write_locked()
                return True
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return False

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            self._sessions.pop(session_id, None)
            self._messages.pop(session_id, None)
            self._session_memory.pop(session_id, None)
            self._session_memory_items.pop(session_id, None)
            self._pg_delete_session_locked(session_id)
            self._queue_disk_flush_locked()
            return True

    def add_message(self, session_id: str, role: str, content: str) -> ChatMessage:
        msg = ChatMessage(
            message_id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            created_at=utc_now_iso(),
        )
        with self._lock:
            self._messages.setdefault(session_id, []).append(msg)
            session = self._sessions.get(session_id)
            if session:
                session.updated_at = utc_now_iso()
                self._sessions[session_id] = session
                self._pg_upsert_session_locked(session)
            self._pg_insert_message_locked(msg)
            self._queue_disk_flush_locked()
        return msg

    def list_messages(self, session_id: str, limit: int = 100) -> List[ChatMessage]:
        with self._lock:
            items = list(self._messages.get(session_id, []))
        return items[-limit:]

    @staticmethod
    def _user_key(tenant_id: str, actor_id: str) -> str:
        return f"{tenant_id}:{actor_id}"

    def get_session_memory(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            base = self._session_memory.get(session_id, {})
            return dict(base) if isinstance(base, dict) else {}

    def upsert_session_memory(
        self,
        session_id: str,
        short_summary: Optional[str] = None,
        long_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            item = self._session_memory.get(session_id) or {}
            if short_summary is not None:
                item["short_summary"] = short_summary
            if long_summary is not None:
                item["long_summary"] = long_summary
            item["updated_at"] = utc_now_iso()
            self._session_memory[session_id] = item
            self._pg_upsert_session_memory_locked(session_id, item)
            self._queue_disk_flush_locked()
            return dict(item)

    def get_user_profile_memory(self, tenant_id: str, actor_id: str) -> Dict[str, Any]:
        with self._lock:
            item = self._user_profile_memory.get(self._user_key(tenant_id, actor_id), {})
            return dict(item) if isinstance(item, dict) else {}

    def upsert_user_profile_memory(
        self,
        tenant_id: str,
        actor_id: str,
        profile_summary: str,
        *,
        last_profile_message_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            key = self._user_key(tenant_id, actor_id)
            item = self._user_profile_memory.get(key) or {}
            item["profile_summary"] = profile_summary
            if last_profile_message_at is not None:
                item["last_profile_message_at"] = str(last_profile_message_at or "").strip() or None
            item["updated_at"] = utc_now_iso()
            self._user_profile_memory[key] = item
            self._pg_upsert_user_profile_locked(tenant_id, actor_id, item)
            self._queue_disk_flush_locked()
            return dict(item)

    def get_user_profile_updated_at(self, tenant_id: str, actor_id: str) -> Optional[str]:
        key = self._user_key(tenant_id, actor_id)
        with self._lock:
            item = self._user_profile_memory.get(key) or {}
            updated_at = str(item.get("updated_at") or "").strip()
            if updated_at:
                return updated_at
            if self._persist_backend == "file_only":
                return None
            if not self._ensure_pg_table():
                return None
            try:
                conn = self._pg_get_conn_locked()
                if conn is None:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT updated_at FROM chat_user_profiles
                        WHERE tenant_id=%s AND actor_id=%s
                        LIMIT 1
                        """,
                        (tenant_id, actor_id),
                    )
                    row = cur.fetchone()
                if not row:
                    return None
                return row[0].isoformat() if row[0] else None
            except Exception:
                self._pg_conn = None
                self._pg_schema_ready = False
                return None

    def list_memory_items(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._session_memory_items.get(session_id, [])
            out: List[Dict[str, Any]] = []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if str(raw.get("conflict_state") or "active") != "active":
                    continue
                out.append(dict(raw))
            return out

    def add_memory_item(
        self,
        session_id: str,
        text: str,
        importance: float,
        kind: str = "episodic",
    ) -> Dict[str, Any]:
        item = {
            "item_id": str(uuid.uuid4()),
            "text": text,
            "importance": float(importance),
            "importance_v2": None,
            "confidence": 0.5,
            "kind": kind,
            "conflict_state": "active",
            "shadowed_by_item_id": None,
            "created_at": utc_now_iso(),
        }
        with self._lock:
            arr = self._session_memory_items.setdefault(session_id, [])
            arr.append(item)
            self._pg_insert_memory_item_locked(session_id, item)
            self._queue_disk_flush_locked()
        return dict(item)

    def apply_memory_eviction(self, session_id: str, max_items: int = 200) -> Dict[str, int]:
        with self._lock:
            items = self._session_memory_items.get(session_id, [])
            total = len(items)
            if total <= max_items:
                return {"total": total, "kept": total, "evicted": 0}
            scored: List[tuple] = []
            # weighted score: importance + recency (not a simple latest-N trim)
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                importance = float(item.get("importance", 0.5))
                recency = (idx + 1) / max(total, 1)
                score = importance * 0.7 + recency * 0.3
                scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            kept_items = [x[1] for x in scored[:max_items]]
            kept_ids = {x.get("item_id") for x in kept_items if isinstance(x, dict)}
            # keep original temporal order for readability
            self._session_memory_items[session_id] = [
                x for x in items if isinstance(x, dict) and x.get("item_id") in kept_ids
            ]
            evicted = total - len(self._session_memory_items[session_id])
            self._pg_replace_memory_items_locked(session_id, self._session_memory_items[session_id])
            self._queue_disk_flush_locked()
            return {"total": total, "kept": len(self._session_memory_items[session_id]), "evicted": evicted}

    @staticmethod
    def _memory_conflict_signature(text: str) -> str:
        raw = str(text or "").strip().lower()
        if not raw:
            return ""
        raw = re.sub(r"^(user asks|assistant answer)\s*:\s*", "", raw)
        raw = re.sub(r"\s+", " ", raw)
        raw = re.sub(r"[^\w\u4e00-\u9fff]+", "", raw)
        return raw

    def resolve_memory_conflicts(self, session_id: str) -> Dict[str, int]:
        sid = str(session_id or "").strip()
        if not sid:
            return {"total": 0, "active": 0, "shadowed": 0}
        with self._lock:
            items = [x for x in self._session_memory_items.get(sid, []) if isinstance(x, dict)]
            if not items:
                return {"total": 0, "active": 0, "shadowed": 0}
            groups: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
            for raw in items:
                sig = self._memory_conflict_signature(str(raw.get("text") or ""))
                kind = str(raw.get("kind") or "")
                if not sig:
                    continue
                groups.setdefault((kind, sig), []).append(raw)

            shadowed_ids: set[str] = set()
            for _, arr in groups.items():
                if len(arr) <= 1:
                    continue
                # Keep the newest one; older duplicates become shadowed.
                arr_sorted = sorted(
                    arr,
                    key=lambda x: str(x.get("created_at") or ""),
                    reverse=True,
                )
                winner = arr_sorted[0]
                winner_id = str(winner.get("item_id") or "")
                for older in arr_sorted[1:]:
                    oid = str(older.get("item_id") or "")
                    if oid:
                        shadowed_ids.add(oid)
                        older["conflict_state"] = "conflict_shadow"
                        older["shadowed_by_item_id"] = winner_id
                winner["conflict_state"] = "active"
                winner["shadowed_by_item_id"] = None

            active = 0
            shadowed = 0
            for raw in items:
                iid = str(raw.get("item_id") or "")
                if iid in shadowed_ids:
                    raw["conflict_state"] = "conflict_shadow"
                    shadowed += 1
                else:
                    raw["conflict_state"] = "active"
                    raw["shadowed_by_item_id"] = None
                    active += 1
            self._session_memory_items[sid] = items
            self._pg_replace_memory_items_locked(sid, items)
            self._queue_disk_flush_locked()
            return {"total": len(items), "active": active, "shadowed": shadowed}

    def enqueue_memory_rerank_job(
        self,
        session_id: str,
        tenant_id: str,
        actor_id: str,
        requested_by: str,
        request_limit: int = 200,
    ) -> Dict[str, Any]:
        sid = str(session_id or "").strip()
        now = utc_now_iso()
        lim = max(1, min(2000, int(request_limit or 200)))
        with self._lock:
            # De-dup by session for pending/running jobs.
            for job in self._memory_rerank_jobs.values():
                if not isinstance(job, dict):
                    continue
                if str(job.get("session_id") or "") != sid:
                    continue
                if str(job.get("status") or "") in {"PENDING", "RUNNING"}:
                    out = dict(job)
                    out["dedup"] = True
                    return out
            if self._persist_backend != "file_only":
                existing = self._pg_find_pending_rerank_job_by_session_locked(sid)
                if existing:
                    self._memory_rerank_jobs[str(existing.get("job_id") or "")] = dict(existing)
                    out = dict(existing)
                    out["dedup"] = True
                    return out
            job = {
                "job_id": str(uuid.uuid4()),
                "session_id": sid,
                "tenant_id": str(tenant_id or ""),
                "actor_id": str(actor_id or ""),
                "requested_by": str(requested_by or ""),
                "request_limit": lim,
                "status": "PENDING",
                "result": None,
                "error_message": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
            }
            self._memory_rerank_jobs[job["job_id"]] = job
            self._pg_insert_memory_rerank_job_locked(job)
            self._queue_disk_flush_locked()
            return dict(job)

    def claim_next_memory_rerank_job(self, worker_id: str) -> Optional[Dict[str, Any]]:
        _ = str(worker_id or "").strip()
        with self._lock:
            pending = [
                dict(x)
                for x in self._memory_rerank_jobs.values()
                if isinstance(x, dict) and str(x.get("status") or "") == "PENDING"
            ]
            pending.sort(key=lambda x: str(x.get("created_at") or ""))
            if not pending:
                if self._persist_backend != "file_only":
                    pg_job = self._pg_claim_memory_rerank_job_locked()
                    if pg_job:
                        self._memory_rerank_jobs[pg_job["job_id"]] = dict(pg_job)
                        return dict(pg_job)
                return None
            picked = pending[0]
            jid = str(picked.get("job_id") or "")
            now = utc_now_iso()
            picked["status"] = "RUNNING"
            picked["started_at"] = now
            picked["updated_at"] = now
            self._memory_rerank_jobs[jid] = picked
            self._pg_update_memory_rerank_job_locked(jid, status="RUNNING", started_at=now, updated_at=now)
            self._queue_disk_flush_locked()
            return dict(picked)

    def complete_memory_rerank_job(
        self,
        job_id: str,
        *,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        jid = str(job_id or "").strip()
        if not jid:
            return False
        with self._lock:
            job = dict(self._memory_rerank_jobs.get(jid) or {})
            if not job:
                return False
            now = utc_now_iso()
            job["status"] = str(status or "FAILED")
            job["result"] = dict(result or {}) if isinstance(result, dict) else None
            job["error_message"] = str(error_message or "")[:2000] or None
            job["finished_at"] = now
            job["updated_at"] = now
            self._memory_rerank_jobs[jid] = job
            self._pg_update_memory_rerank_job_locked(
                jid,
                status=job["status"],
                result=job["result"],
                error_message=job["error_message"],
                finished_at=now,
                updated_at=now,
            )
            self._queue_disk_flush_locked()
            return True

    def get_memory_rerank_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        jid = str(job_id or "").strip()
        with self._lock:
            local = self._memory_rerank_jobs.get(jid)
            if isinstance(local, dict):
                return dict(local)
            if self._persist_backend != "file_only":
                pg_job = self._pg_get_memory_rerank_job_locked(jid)
                if pg_job:
                    self._memory_rerank_jobs[jid] = dict(pg_job)
                    return dict(pg_job)
            return None

    def list_memory_rerank_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(500, int(limit or 50)))
        with self._lock:
            jobs = [dict(x) for x in self._memory_rerank_jobs.values() if isinstance(x, dict)]
            jobs.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            if jobs:
                return jobs[:lim]
            if self._persist_backend != "file_only":
                return self._pg_list_memory_rerank_jobs_locked(limit=lim)
            return []

    def create_run(self, session_id: str, tenant_id: str, actor_id: str, mode: str) -> ChatRunState:
        run = ChatRunState(
            run_id=str(uuid.uuid4()),
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            mode=mode,
            started_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._cancel_flags[run.run_id] = False
        self._upsert_run_to_postgres(run)
        return run

    def get_run(self, run_id: str) -> Optional[ChatRunState]:
        with self._lock:
            return self._runs.get(run_id)

    def update_run(self, run_id: str, **kwargs) -> Optional[ChatRunState]:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return None
            for k, v in kwargs.items():
                setattr(run, k, v)
            run.updated_at = utc_now_iso()
            self._runs[run_id] = run
            self._upsert_run_to_postgres(run)
            return run

    def complete_run(self, run_id: str, status: str, error_message: Optional[str] = None) -> Optional[ChatRunState]:
        return self.update_run(
            run_id,
            status=status,
            error_message=error_message,
            completed_at=utc_now_iso(),
        )

    def cancel_run(self, run_id: str) -> bool:
        with self._lock:
            if run_id not in self._runs:
                return False
            self._cancel_flags[run_id] = True
            run = self._runs[run_id]
            run.stage = "CANCELLING"
            self._runs[run_id] = run
            self._upsert_run_to_postgres(run)
            return True

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return bool(self._cancel_flags.get(run_id, False))


chat_store = ChatStore()
