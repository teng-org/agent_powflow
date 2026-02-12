from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import queue
import threading
from urllib.parse import parse_qs

from fastapi import Request


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _safe(value: object) -> str:
    text = str(value if value is not None else "")
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _hash12(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]


def _pairs_text(pairs: dict[str, object]) -> str:
    items = []
    for key, value in pairs.items():
        if value is None:
            continue
        items.append(f"{_safe(key)}={_safe(value)}")
    return ";".join(items) if items else "-"


def route_meta(method: str, path_template: str) -> dict[str, str]:
    mapping: dict[tuple[str, str], dict[str, str]] = {
        ("POST", "/api/v1/admin/auth/login"): {"event": "kb.auth.login", "action": "login", "resource_type": "auth"},
        ("POST", "/api/v1/admin/auth/logout"): {"event": "kb.auth.logout", "action": "logout", "resource_type": "auth"},
        ("GET", "/api/v1/admin/kbs"): {"event": "kb.kb.list", "action": "read", "resource_type": "kb"},
        ("POST", "/api/v1/admin/kbs"): {"event": "kb.kb.create", "action": "create", "resource_type": "kb"},
        ("PATCH", "/api/v1/admin/kbs/{kb_id}"): {"event": "kb.kb.rename", "action": "update", "resource_type": "kb"},
        ("DELETE", "/api/v1/admin/kbs/{kb_id}"): {"event": "kb.kb.delete", "action": "delete", "resource_type": "kb"},
        ("POST", "/api/v1/admin/kbs/{kb_id}/documents/upload"): {"event": "kb.document.upload", "action": "upload", "resource_type": "document"},
        ("POST", "/api/v1/admin/kbs/{kb_id}/documents/ingest"): {"event": "kb.document.ingest", "action": "create", "resource_type": "document"},
        ("GET", "/api/v1/admin/kbs/{kb_id}/documents"): {"event": "kb.document.list", "action": "read", "resource_type": "document"},
        ("DELETE", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}"): {"event": "kb.document.delete", "action": "delete", "resource_type": "document"},
        ("POST", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/reindex"): {"event": "kb.document.reindex", "action": "update", "resource_type": "document"},
        ("POST", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunk"): {"event": "kb.document.chunk", "action": "update", "resource_type": "document"},
        ("GET", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks"): {"event": "kb.chunk.list", "action": "read", "resource_type": "chunk"},
        ("PUT", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft"): {"event": "kb.chunk.draft.update", "action": "update", "resource_type": "chunk"},
        ("DELETE", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft"): {"event": "kb.chunk.draft.clear", "action": "delete", "resource_type": "chunk"},
        ("POST", "/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/commit"): {"event": "kb.chunk.commit", "action": "commit", "resource_type": "chunk"},
        ("GET", "/api/v1/admin/tasks"): {"event": "kb.task.list", "action": "read", "resource_type": "task"},
        ("DELETE", "/api/v1/admin/tasks"): {"event": "kb.task.clear", "action": "delete", "resource_type": "task"},
        ("POST", "/api/v1/admin/tasks/{task_id}/cancel"): {"event": "kb.task.cancel", "action": "cancel", "resource_type": "task"},
        ("GET", "/api/v1/admin/tasks/{task_id}/events"): {"event": "kb.task.events", "action": "read", "resource_type": "task"},
        ("PUT", "/api/v1/admin/system/upload-settings"): {"event": "kb.setting.upload.update", "action": "update", "resource_type": "setting"},
    }
    return mapping.get((method.upper(), path_template), {"event": "kb.api.call", "action": "call", "resource_type": "api"})


def summarize_headers(headers) -> str:
    allow = (
        "x-tenant-id",
        "x-actor-id",
        "x-trace-id",
        "x-source-service",
        "content-type",
        "content-length",
        "authorization",
    )
    out: dict[str, str] = {}
    for key in allow:
        raw = headers.get(key)
        if raw is None:
            continue
        if key == "authorization":
            out[key] = "Bearer***" if str(raw).startswith("Bearer ") else "***"
        else:
            out[key] = str(raw)
    return _pairs_text(out)


def summarize_query(query_string: str) -> str:
    if not query_string:
        return "-"
    parsed = parse_qs(query_string, keep_blank_values=False)
    allow = {"kb_id", "doc_id", "task_id", "limit", "top_k"}
    out: dict[str, str] = {}
    for key in allow:
        values = parsed.get(key)
        if not values:
            continue
        out[key] = ",".join([_safe(v) for v in values[:5]])
    return _pairs_text(out)


async def summarize_body(request: Request, path_template: str, max_inspect_bytes: int = 65536) -> str:
    method = request.method.upper()
    if method in {"GET", "DELETE", "OPTIONS", "HEAD"}:
        return "-"
    content_type = (request.headers.get("content-type") or "").lower()
    content_length = int(request.headers.get("content-length") or "0")
    if "multipart/form-data" in content_type:
        return _pairs_text({"kind": "multipart", "content_length": content_length})
    if content_length > max_inspect_bytes:
        return _pairs_text({"kind": "large", "content_length": content_length, "sha256_12": "skipped"})

    raw = await request.body()
    if not raw:
        return "-"
    if len(raw) > max_inspect_bytes:
        return _pairs_text({"kind": "large", "content_length": len(raw), "sha256_12": _hash12(raw.decode("utf-8", errors="ignore"))})

    if "application/json" not in content_type:
        return _pairs_text({"kind": "raw", "content_length": len(raw), "sha256_12": _hash12(raw.decode("utf-8", errors="ignore"))})

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return _pairs_text({"kind": "json", "content_length": len(raw), "parse": "failed"})
    if not isinstance(data, dict):
        return _pairs_text({"kind": "json", "keys": "-", "content_length": len(raw)})

    keys = ",".join(sorted(data.keys())[:20]) or "-"
    summary: dict[str, object] = {"kind": "json", "keys": keys, "content_length": len(raw)}
    if path_template == "/api/v1/admin/auth/login":
        summary["username_hash"] = _hash12(str(data.get("username", "")))
    elif path_template in {"/api/v1/admin/kbs", "/api/v1/admin/kbs/{kb_id}"}:
        name = str(data.get("name", ""))
        summary["name_len"] = len(name)
        summary["name_hash"] = _hash12(name)
    elif path_template.endswith("/documents/ingest"):
        content = str(data.get("content", ""))
        summary["doc_id"] = _safe(data.get("doc_id", ""))
        summary["filename_hash"] = _hash12(str(data.get("filename", "")))
        summary["content_len"] = len(content)
        summary["content_sha256_12"] = _hash12(content)
    elif path_template.endswith("/documents/{doc_id}/reindex"):
        summary["chunk_size"] = data.get("chunk_size")
        summary["chunk_overlap"] = data.get("chunk_overlap")
    elif path_template.endswith("/chunks/{chunk_id}/draft"):
        text = str(data.get("text", ""))
        summary["action"] = _safe(data.get("action", ""))
        summary["text_len"] = len(text)
        summary["text_sha256_12"] = _hash12(text) if text else "-"
    return _pairs_text(summary)


def summarize_error_response_body(status_code: int, content_type: str, body: bytes) -> tuple[str, str]:
    if status_code < 400:
        return "", ""
    if not body:
        return "", ""
    if "application/json" in (content_type or "").lower():
        try:
            data = json.loads(body.decode("utf-8"))
            detail = data.get("detail") or data.get("error") or data.get("message") or ""
            return ("HTTPError", _safe(detail))
        except Exception:
            return ("HTTPError", "json parse failed")
    return ("HTTPError", _safe(body.decode("utf-8", errors="replace")[:200]))


def render_ltsv(record: dict[str, object]) -> str:
    ordered = [
        "ts",
        "level",
        "log_type",
        "event",
        "service",
        "env",
        "request_id",
        "trace_id",
        "source_service",
        "tenant_id",
        "actor_id",
        "actor_role",
        "auth_result",
        "permission",
        "method",
        "path",
        "query",
        "headers",
        "body",
        "resource_type",
        "resource_id",
        "action",
        "outcome",
        "http_status",
        "biz_code",
        "duration_ms",
        "error_class",
        "error_reason",
        "upstream",
    ]
    items = []
    for key in ordered:
        if key not in record:
            continue
        value = record.get(key)
        if value is None or value == "":
            continue
        items.append(f"{key}:{_safe(value)}")
    return "\t".join(items)


class AuditSink:
    def __init__(self, enabled: bool, base_dir: str, service: str = "execution-api", env: str = "dev") -> None:
        self.enabled = enabled
        self.base_dir = base_dir
        self.service = service
        self.env = env
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=10000)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="kb-audit-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        try:
            self._queue.put_nowait(("__stop__", ""))
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def emit(self, log_type: str, record: dict[str, object]) -> None:
        if not self.enabled:
            return
        rec = dict(record)
        rec["log_type"] = log_type
        rec["service"] = self.service
        rec["env"] = self.env
        line = render_ltsv(rec)
        if not line:
            return
        try:
            self._queue.put_nowait((log_type, line))
        except queue.Full:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                log_type, line = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if log_type == "__stop__":
                return
            try:
                self._write_line(log_type, line)
            except Exception:
                pass

    def _write_line(self, log_type: str, line: str) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        day_dir = os.path.join(self.base_dir, day)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{log_type}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def extra_log_types(event: str, outcome: str, auth_result: str) -> list[str]:
    items: list[str] = []
    if event.startswith("kb.auth."):
        items.append("auth")
    if outcome == "FAIL":
        items.append("error")
    if auth_result == "DENY":
        items.append("security")
    if event in {"kb.kb.delete", "kb.task.clear", "kb.document.delete"}:
        items.append("security")
    return sorted(set(items))


def build_base_record(
    request: Request,
    path_template: str,
    body_summary: str,
    response_status: int,
    duration_ms: int,
    error_class: str = "",
    error_reason: str = "",
) -> dict[str, object]:
    meta = route_meta(request.method, path_template)
    auth_result = _safe(getattr(request.state, "audit_auth_result", "ANON") or "ANON")
    permission = _safe(getattr(request.state, "audit_permission", ""))
    actor_role = _safe(getattr(request.state, "audit_actor_role", ""))
    outcome = "SUCCESS" if response_status < 400 else "FAIL"
    biz_code = "OK" if response_status < 400 else f"HTTP_{response_status}"
    if response_status == 401:
        biz_code = "UNAUTHORIZED"
    elif response_status == 403:
        biz_code = "FORBIDDEN"
    elif response_status == 404:
        biz_code = "NOT_FOUND"
    elif response_status == 400:
        biz_code = "BAD_REQUEST"
    path_params = getattr(request, "path_params", {}) or {}
    inferred_resource_id = ""
    for key in ("kb_id", "doc_id", "task_id", "chunk_id", "endpoint_id"):
        value = path_params.get(key)
        if value:
            inferred_resource_id = str(value)
            break
    return {
        "ts": _now_iso(),
        "level": "ERROR" if response_status >= 500 else ("WARN" if response_status >= 400 else "INFO"),
        "event": meta["event"],
        "request_id": getattr(request.state, "request_id", ""),
        "trace_id": getattr(request.state, "trace_id", ""),
        "source_service": getattr(request.state, "source_service", ""),
        "tenant_id": getattr(request.state, "tenant_id", ""),
        "actor_id": getattr(request.state, "actor_id", ""),
        "actor_role": actor_role,
        "auth_result": auth_result,
        "permission": permission,
        "method": request.method.upper(),
        "path": path_template,
        "query": summarize_query(request.url.query),
        "headers": summarize_headers(request.headers),
        "body": body_summary,
        "resource_type": meta["resource_type"],
        "resource_id": _safe(getattr(request.state, "audit_resource_id", "") or inferred_resource_id),
        "action": meta["action"],
        "outcome": outcome,
        "http_status": response_status,
        "biz_code": biz_code,
        "duration_ms": duration_ms,
        "error_class": error_class,
        "error_reason": error_reason,
        "upstream": _safe(getattr(request.state, "audit_upstream", "")),
    }
