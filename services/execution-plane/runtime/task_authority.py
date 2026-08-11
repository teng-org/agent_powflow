from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from runtime.control_plane_client import control_plane_client


def _enabled() -> bool:
    return os.getenv("TASK_AUTHORITY_CALLBACK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


class TaskAuthorityBridge:
    """Route Task mutations to Control while retaining an explicit local-test fallback."""

    def __init__(self, store) -> None:
        self.store = store

    def ensure_task(
        self,
        task_id: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        task_type: str,
        stage: str,
        *,
        filename: str = "",
    ) -> bool:
        existing = self.store.get_task(task_id) or {}
        if not _enabled() or (existing and not self._is_control_owned(existing)):
            return self.store.create_task_record(
                task_id, tenant_id, kb_id, doc_id, task_type, stage, filename=filename
            )
        current_status = str(existing.get("status") or "PENDING")
        current_stage = str(existing.get("stage") or stage)
        self._emit(
            task_id,
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            task_type=task_type,
            filename=filename,
            event_type="TASK_ENSURE",
            # A redelivered Celery message must not regress a RUNNING or
            # terminal Control-owned task back to PENDING.
            status=current_status,
            stage=current_stage,
            payload={"task_type": task_type},
        )
        return True

    def update_task_record(self, task_id: str, status: str, stage: str, error_message: str | None = None) -> None:
        if not self._uses_control(task_id):
            self.store.update_task_record(task_id, status, stage, error_message=error_message)
            return
        identity = self._identity(task_id)
        self._emit(
            task_id,
            **identity,
            event_type="TASK_STATUS",
            status=status,
            stage=stage,
            error_message=error_message,
            payload={"status": status, "error_message": error_message},
        )

    def update_task_progress(
        self, task_id: str, stage: str, current: int, total: int, progress_pct: int | None = None
    ) -> None:
        if not self._uses_control(task_id):
            self.store.update_task_progress(task_id, stage, current, total, progress_pct)
            return
        pct = max(0, min(100, int(progress_pct if progress_pct is not None else ((current * 100 / total) if total else 0))))
        identity = self._identity(task_id)
        self._emit(
            task_id,
            **identity,
            event_type="TASK_PROGRESS_STATE",
            status="RUNNING",
            stage=stage,
            progress_current=max(0, int(current or 0)),
            progress_total=max(0, int(total or 0)),
            progress_pct=pct,
            payload={"current": current, "total": total, "progress_pct": pct},
        )

    def append_task_event(
        self,
        task_id: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        event_type: str,
        stage: str,
        payload: dict,
    ) -> None:
        if not self._uses_control(task_id):
            self.store.append_task_event(task_id, tenant_id, kb_id, doc_id, event_type, stage, payload)
            return
        identity = self._identity(task_id)
        self._emit(
            task_id,
            tenant_id=tenant_id or identity["tenant_id"],
            kb_id=kb_id or identity["kb_id"],
            doc_id=doc_id if doc_id != "" else identity["doc_id"],
            task_type=identity["task_type"],
            filename=identity["filename"],
            event_type=event_type,
            # Event payload is business data. A payload field named "status"
            # (for example document status=INDEXED) must never mutate Task state.
            status=None,
            stage=stage,
            payload=payload or {},
        )

    def _identity(self, task_id: str) -> dict[str, str]:
        task = self.store.get_task(task_id) or {}
        if not task:
            raise RuntimeError("task identity is unavailable for Control callback")
        return {
            "tenant_id": str(task.get("tenant_id") or ""),
            "kb_id": str(task.get("kb_id") or ""),
            "doc_id": str(task.get("doc_id") or ""),
            "task_type": str(task.get("task_type") or ""),
            "filename": str(task.get("filename") or ""),
        }

    def _uses_control(self, task_id: str) -> bool:
        if not _enabled():
            return False
        task = self.store.get_task(task_id) or {}
        return self._is_control_owned(task)

    @staticmethod
    def _is_control_owned(task: dict) -> bool:
        return str(task.get("state_owner") or "").strip().upper() == "CONTROL"

    def _emit(self, task_id: str, **event: Any) -> dict:
        normalized = {key: value for key, value in event.items() if value is not None}
        normalized.setdefault("payload", {})
        normalized["source_service"] = "execution-plane"
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        normalized["event_id"] = hashlib.sha256(f"{task_id}|{canonical}".encode("utf-8")).hexdigest()
        return control_plane_client.append_task_event(task_id, normalized)
