from __future__ import annotations

import json
import os
import threading
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
        self._runs: Dict[str, ChatRunState] = {}
        self._cancel_flags: Dict[str, bool] = {}
        self._persist_backend = self._resolve_store_backend()
        self._postgres_dsn = self._resolve_postgres_dsn()
        self._store_path = self._resolve_store_path()
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

    def _payload_locked(self) -> Dict[str, Any]:
        return {
            "sessions": {k: v.model_dump() for k, v in self._sessions.items()},
            "messages": {k: [m.model_dump() for m in items] for k, items in self._messages.items()},
            "session_memory": self._session_memory,
            "user_profile_memory": self._user_profile_memory,
            "session_memory_items": self._session_memory_items,
        }

    def _load_from_payload(self, payload: Dict[str, Any]) -> bool:
        try:
            sessions = payload.get("sessions", {})
            messages = payload.get("messages", {})
            session_memory = payload.get("session_memory", {})
            user_profile_memory = payload.get("user_profile_memory", {})
            session_memory_items = payload.get("session_memory_items", {})

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
        try:
            with psycopg.connect(self._postgres_dsn, autocommit=True) as conn:
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
            return True
        except Exception:
            return False

    def _load_from_postgres(self) -> bool:
        if not self._ensure_pg_table():
            return False
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

    def _save_to_postgres_locked(self, payload: Dict[str, Any]) -> bool:
        if not self._ensure_pg_table():
            return False
        try:
            with psycopg.connect(self._postgres_dsn, autocommit=True) as conn:
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
            return True
        except Exception:
            return False

    def _save_to_disk_locked(self, payload: Dict[str, Any]) -> bool:
        try:
            if not self._store_path.parent.exists():
                self._store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store_path.with_suffix(self._store_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(self._store_path))
            return True
        except Exception:
            return False

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
            self._user_profile_memory.setdefault(user_key, {"profile_summary": "", "updated_at": now})
            self._session_memory_items.setdefault(session.session_id, [])
            self._save_persistent_locked()
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
            self._save_persistent_locked()

    def rename_session(self, session_id: str, title: str) -> Optional[ChatSession]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            session.title = title
            session.updated_at = utc_now_iso()
            self._sessions[session_id] = session
            self._save_persistent_locked()
            return session

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            self._sessions.pop(session_id, None)
            self._messages.pop(session_id, None)
            self._session_memory.pop(session_id, None)
            self._session_memory_items.pop(session_id, None)
            self._save_persistent_locked()
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
            self.touch_session(session_id)
            self._save_persistent_locked()
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
            self._save_persistent_locked()
            return dict(item)

    def get_user_profile_memory(self, tenant_id: str, actor_id: str) -> Dict[str, Any]:
        with self._lock:
            item = self._user_profile_memory.get(self._user_key(tenant_id, actor_id), {})
            return dict(item) if isinstance(item, dict) else {}

    def upsert_user_profile_memory(self, tenant_id: str, actor_id: str, profile_summary: str) -> Dict[str, Any]:
        with self._lock:
            key = self._user_key(tenant_id, actor_id)
            item = self._user_profile_memory.get(key) or {}
            item["profile_summary"] = profile_summary
            item["updated_at"] = utc_now_iso()
            self._user_profile_memory[key] = item
            self._save_persistent_locked()
            return dict(item)

    def list_memory_items(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._session_memory_items.get(session_id, [])
            return [dict(x) for x in items if isinstance(x, dict)]

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
            "kind": kind,
            "created_at": utc_now_iso(),
        }
        with self._lock:
            arr = self._session_memory_items.setdefault(session_id, [])
            arr.append(item)
            self._save_persistent_locked()
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
            self._save_persistent_locked()
            return {"total": total, "kept": len(self._session_memory_items[session_id]), "evicted": evicted}

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
            return True

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return bool(self._cancel_flags.get(run_id, False))


chat_store = ChatStore()
