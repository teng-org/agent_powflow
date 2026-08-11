from __future__ import annotations

import json
import os
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

from .config import get_setting
from .schema_migrations import SchemaMigrationError, require_database_current

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatApiEndpoint:
    endpoint_id: str
    name: str
    provider: str
    base_url: str
    model: str
    task: str
    api_key: str
    api_instance_id: str = ""
    api_instance_name: str = ""
    api_instance_remark: str = ""
    key_fingerprint: str = ""
    tenant_id: str = "*"
    enabled: bool = True
    frozen: bool = False
    priority: int = 100
    weight: int = 100
    max_concurrency: int = 8
    timeout_seconds: int = 120
    retry_max: int = 2
    circuit_fail_threshold: int = 5
    circuit_open_seconds: int = 30
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ChatApiPoolSnapshot:
    default_endpoint_id: str
    default_endpoint_ids: Dict[str, str]
    endpoints: List[ChatApiEndpoint]


@dataclass
class ChatApiAlertRule:
    rule_id: str
    name: str
    enabled: bool
    metric: str
    scope: str
    tenant_id: str = "*"
    task: str = ""
    endpoint_id: str = ""
    model: str = ""
    threshold: float = 0.0
    window_days: int = 1
    comparison: str = "gte"
    severity: str = "warning"
    cooldown_seconds: int = 300
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ChatApiAlertEvent:
    event_id: str
    rule_id: str
    rule_name: str
    severity: str
    metric: str
    scope: str
    message: str
    value: float
    threshold: float
    tenant_id: str = "*"
    task: str = ""
    endpoint_id: str = ""
    model: str = ""
    acked: bool = False
    acked_by: str = ""
    acked_at: str = ""
    created_at: str = ""


@dataclass
class ChatApiCallLog:
    log_id: str
    tenant_id: str
    task: str
    endpoint_id: str
    model: str
    base_url: str
    path: str
    ok: bool
    status_code: int
    error: str
    latency_ms: float
    retry_count: int
    input_tokens: int
    output_tokens: int
    cost_total: float
    request_summary: str
    response_summary: str
    response_headers_ms: float = 0.0
    first_token_ms: float = 0.0
    trace_id: str = ""
    created_at: str = ""


class ChatApiPoolManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._endpoints: Dict[str, ChatApiEndpoint] = {}
        self._default_endpoint_id: str = ""
        self._default_endpoint_ids: Dict[str, str] = {}
        self._inflight: Dict[str, int] = {}
        self._health: Dict[str, Dict[str, Any]] = {}
        self._daily_usage: Dict[str, Dict[str, float]] = {}
        self._alert_rules: Dict[str, ChatApiAlertRule] = {}
        self._alert_events: List[ChatApiAlertEvent] = []
        self._last_alert_fired_at: Dict[str, float] = {}
        self._call_logs: List[ChatApiCallLog] = []
        self._rr_counters: Dict[str, int] = {}
        self._state_path = Path(
            str(
                os.getenv(
                    "CHAT_API_POOL_STATE_PATH",
                    "/app/data/chat-plane/chat_api_pool_state.json",
                )
            )
        )
        self._postgres_dsn = self._resolve_postgres_dsn()
        self._pg_conn = None
        self._pg_schema_ready = False

    def _resolve_postgres_dsn(self) -> str:
        candidates = [
            str(get_setting("chat_history_postgres_dsn", "")).strip(),
            str(os.getenv("CHAT_HISTORY_POSTGRES_DSN", "")).strip(),
            str(os.getenv("POSTGRES_DSN", "")).strip(),
            str(get_setting("postgres_dsn", "")).strip(),
        ]
        for one in candidates:
            if one:
                return one
        return ""

    @staticmethod
    def _fingerprint_api_key(api_key: str) -> str:
        value = str(api_key or "").strip()
        if not value:
            return ""
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:12]}"

    @staticmethod
    def _normalize_endpoint(endpoint: ChatApiEndpoint) -> ChatApiEndpoint:
        now = _utc_iso()
        endpoint.tenant_id = str(endpoint.tenant_id or "*").strip() or "*"
        endpoint.api_key = str(endpoint.api_key or "").strip()
        endpoint.api_instance_id = str(endpoint.api_instance_id or endpoint.endpoint_id or "").strip()
        endpoint.api_instance_name = str(endpoint.api_instance_name or endpoint.name or endpoint.api_instance_id).strip()
        endpoint.api_instance_remark = str(endpoint.api_instance_remark or "").strip()
        endpoint.key_fingerprint = ChatApiPoolManager._fingerprint_api_key(endpoint.api_key)
        endpoint.priority = int(max(1, endpoint.priority or 100))
        endpoint.weight = int(max(1, endpoint.weight or 100))
        endpoint.max_concurrency = int(max(1, endpoint.max_concurrency or 8))
        endpoint.timeout_seconds = int(max(5, endpoint.timeout_seconds or 120))
        endpoint.retry_max = int(max(0, min(endpoint.retry_max if endpoint.retry_max is not None else 2, 5)))
        endpoint.circuit_fail_threshold = int(max(1, endpoint.circuit_fail_threshold or 5))
        endpoint.circuit_open_seconds = int(max(5, endpoint.circuit_open_seconds or 30))
        endpoint.created_at = str(endpoint.created_at or now)
        endpoint.updated_at = now
        return endpoint

    @staticmethod
    def _normalize_rule(rule: ChatApiAlertRule) -> ChatApiAlertRule:
        now = _utc_iso()
        allowed_metrics = {
            "daily_cost_total",
            "daily_total_tokens",
            "error_rate",
            "success_rate",
            "latency_p95_approx_ms",
        }
        allowed_scopes = {"global", "tenant", "task", "endpoint", "model"}
        allowed_comp = {"gte", "lte"}
        allowed_severity = {"info", "warning", "critical"}
        rule.metric = str(rule.metric or "daily_cost_total").strip()
        if rule.metric not in allowed_metrics:
            rule.metric = "daily_cost_total"
        rule.scope = str(rule.scope or "global").strip()
        if rule.scope not in allowed_scopes:
            rule.scope = "global"
        rule.comparison = str(rule.comparison or "gte").strip()
        if rule.comparison not in allowed_comp:
            rule.comparison = "gte"
        rule.severity = str(rule.severity or "warning").strip()
        if rule.severity not in allowed_severity:
            rule.severity = "warning"
        rule.window_days = int(max(1, min(30, rule.window_days or 1)))
        rule.cooldown_seconds = int(max(30, min(86400, rule.cooldown_seconds or 300)))
        rule.threshold = float(max(0.0, rule.threshold or 0.0))
        rule.tenant_id = str(rule.tenant_id or "*").strip() or "*"
        rule.task = str(rule.task or "").strip()
        rule.endpoint_id = str(rule.endpoint_id or "").strip()
        rule.model = str(rule.model or "").strip()
        rule.created_at = str(rule.created_at or now)
        rule.updated_at = now
        return rule

    def _save_state_locked(self) -> None:
        payload = {
            "default_endpoint_id": self._default_endpoint_id,
            "default_endpoint_ids": dict(self._default_endpoint_ids),
            "endpoints": [x.__dict__.copy() for x in self._endpoints.values()],
            "health": dict(self._health),
            "daily_usage": dict(self._daily_usage),
            "alert_rules": [x.__dict__.copy() for x in self._alert_rules.values()],
            "alert_events": [x.__dict__.copy() for x in self._alert_events[-5000:]],
            "call_logs": [x.__dict__.copy() for x in self._call_logs[-5000:]],
            "updated_at": _utc_iso(),
        }
        self._save_state_to_postgres_locked(payload)
        self._save_state_to_file_locked(payload)

    def _save_state_to_file_locked(self, payload: Dict) -> None:
        try:
            parent = self._state_path.parent
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

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

    def _pg_ensure_schema_locked(self) -> bool:
        if psycopg is None or not self._postgres_dsn:
            return False
        if self._pg_schema_ready:
            return True
        try:
            require_database_current(self._postgres_dsn)
            self._pg_schema_ready = True
            return True
        except SchemaMigrationError:
            raise
        except Exception:
            self._pg_schema_ready = False
            self._pg_conn = None
            return False
    def _save_state_to_postgres_locked(self, payload: Dict) -> None:
        if not self._pg_ensure_schema_locked():
            return
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_api_pool_state(snapshot_id, payload, updated_at)
                    VALUES(1, %s::jsonb, NOW())
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                      payload=EXCLUDED.payload,
                      updated_at=NOW()
                    """,
                    (json.dumps(payload, ensure_ascii=False),),
                )
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False

    def _load_state_from_postgres_locked(self) -> bool:
        if not self._pg_ensure_schema_locked():
            return False
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM chat_api_pool_state WHERE snapshot_id=1 LIMIT 1"
                )
                row = cur.fetchone()
            if not row:
                return False
            payload = row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                return False
            return self._load_state_payload_locked(payload)
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return False

    def _read_state_from_postgres_locked(self) -> Optional[Dict]:
        if not self._pg_ensure_schema_locked():
            return None
        try:
            conn = self._pg_get_conn_locked()
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM chat_api_pool_state WHERE snapshot_id=1 LIMIT 1"
                )
                row = cur.fetchone()
            if not row:
                return None
            payload = row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return payload if isinstance(payload, dict) else None
        except Exception:
            self._pg_conn = None
            self._pg_schema_ready = False
            return None

    def _read_state_from_file_locked(self) -> Optional[Dict]:
        p = self._state_path
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _payload_score(payload: Optional[Dict]) -> Tuple[float, int, int]:
        if not isinstance(payload, dict):
            return (0.0, 0, 0)
        updated_at = str(payload.get("updated_at") or "")
        try:
            updated_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp() if updated_at else 0.0
        except Exception:
            updated_ts = 0.0
        endpoints = payload.get("endpoints") or []
        key_count = 0
        endpoint_count = 0
        if isinstance(endpoints, list):
            endpoint_count = len([x for x in endpoints if isinstance(x, dict)])
            key_count = len(
                [
                    x
                    for x in endpoints
                    if isinstance(x, dict) and str(x.get("api_key") or "").strip()
                ]
            )
        return (updated_ts, key_count, endpoint_count)

    @staticmethod
    def _merge_endpoint_keys(primary: Dict, fallback: Optional[Dict]) -> Dict:
        if not isinstance(fallback, dict):
            return primary
        fallback_endpoints = fallback.get("endpoints") or []
        if not isinstance(fallback_endpoints, list):
            return primary
        fallback_by_id = {
            str(x.get("endpoint_id") or ""): x
            for x in fallback_endpoints
            if isinstance(x, dict) and str(x.get("endpoint_id") or "")
        }
        endpoints = primary.get("endpoints") or []
        if not isinstance(endpoints, list):
            return primary
        for item in endpoints:
            if not isinstance(item, dict):
                continue
            if str(item.get("api_key") or "").strip():
                continue
            fallback_item = fallback_by_id.get(str(item.get("endpoint_id") or ""))
            fallback_key = str((fallback_item or {}).get("api_key") or "").strip()
            if fallback_key:
                item["api_key"] = fallback_key
        return primary

    def _load_state_payload_locked(self, payload: Dict) -> bool:
        try:
            raw_endpoints = payload.get("endpoints") or []
            if isinstance(raw_endpoints, list):
                eps: Dict[str, ChatApiEndpoint] = {}
                for one in raw_endpoints:
                    if not isinstance(one, dict):
                        continue
                    ep = self._normalize_endpoint(ChatApiEndpoint(**one))
                    eps[ep.endpoint_id] = ep
                if eps:
                    self._endpoints = eps
            self._default_endpoint_id = str(payload.get("default_endpoint_id") or self._default_endpoint_id or "")
            default_ids = payload.get("default_endpoint_ids")
            if isinstance(default_ids, dict):
                self._default_endpoint_ids = dict(default_ids)

            health = payload.get("health")
            if isinstance(health, dict):
                self._health = {
                    str(k): dict(v) for k, v in health.items() if isinstance(v, dict)
                }
                # A half-open probe claim belongs to the current process only. If
                # the process stopped while a probe was in flight, restoring the
                # persisted claim would leave the endpoint permanently unusable.
                for item in self._health.values():
                    item["half_open_inflight"] = False
            usage = payload.get("daily_usage")
            if isinstance(usage, dict):
                self._daily_usage = {
                    str(k): dict(v) for k, v in usage.items() if isinstance(v, dict)
                }
            rules = payload.get("alert_rules")
            if isinstance(rules, list):
                parsed_rules: Dict[str, ChatApiAlertRule] = {}
                for one in rules:
                    if not isinstance(one, dict):
                        continue
                    rule = self._normalize_rule(ChatApiAlertRule(**one))
                    parsed_rules[rule.rule_id] = rule
                self._alert_rules = parsed_rules
            events = payload.get("alert_events")
            if isinstance(events, list):
                parsed_events: List[ChatApiAlertEvent] = []
                for one in events:
                    if not isinstance(one, dict):
                        continue
                    try:
                        parsed_events.append(ChatApiAlertEvent(**one))
                    except Exception:
                        continue
                self._alert_events = parsed_events[-5000:]
            logs = payload.get("call_logs")
            if isinstance(logs, list):
                parsed_logs: List[ChatApiCallLog] = []
                for one in logs:
                    if not isinstance(one, dict):
                        continue
                    try:
                        parsed_logs.append(ChatApiCallLog(**one))
                    except Exception:
                        continue
                self._call_logs = parsed_logs[-5000:]
            return True
        except Exception:
            return False

    def load_state(self) -> bool:
        with self._lock:
            pg_payload = self._read_state_from_postgres_locked()
            file_payload = self._read_state_from_file_locked()
            candidates = [x for x in (pg_payload, file_payload) if isinstance(x, dict)]
            if not candidates:
                return False
            payload = max(candidates, key=self._payload_score)
            fallback = file_payload if payload is pg_payload else pg_payload
            payload = self._merge_endpoint_keys(payload, fallback)
            loaded = self._load_state_payload_locked(payload)
            if loaded:
                self._save_state_locked()
            return loaded

    def reset_from_file(self, path: str) -> ChatApiPoolSnapshot:
        raise RuntimeError("Chat API pool file loading is disabled; use API management database")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        raw_endpoints = data.get("endpoints") or []
        endpoints: List[ChatApiEndpoint] = []
        for item in raw_endpoints:
            if not isinstance(item, dict):
                continue
            ep = ChatApiEndpoint(**item)
            endpoints.append(self._normalize_endpoint(ep))
        with self._lock:
            self._endpoints = {e.endpoint_id: e for e in endpoints}
            self._default_endpoint_id = str(data.get("default_endpoint_id") or "")
            self._default_endpoint_ids = dict(data.get("default_endpoint_ids") or {})
            if not self._default_endpoint_ids and self._default_endpoint_id:
                self._default_endpoint_ids = {"chat_llm": self._default_endpoint_id}
            for eid in list(self._health.keys()):
                if eid not in self._endpoints:
                    self._health.pop(eid, None)
            for eid in self._endpoints:
                self._inflight.setdefault(eid, 0)
                self._health.setdefault(
                    eid,
                    {
                        "success_count": 0.0,
                        "fail_count": 0.0,
                        "consecutive_failures": 0.0,
                        "last_latency_ms": 0.0,
                        "latency_ema_ms": 0.0,
                        "last_error_at_ts": 0.0,
                        "circuit_open_until_ts": 0.0,
                        "updated_at_ts": 0.0,
                    },
                )
        return self.snapshot()

    def snapshot(self) -> ChatApiPoolSnapshot:
        with self._lock:
            return ChatApiPoolSnapshot(
                default_endpoint_id=self._default_endpoint_id,
                default_endpoint_ids=dict(self._default_endpoint_ids),
                endpoints=list(self._endpoints.values()),
            )

    def list_endpoints(self, task: Optional[str] = None) -> List[ChatApiEndpoint]:
        with self._lock:
            items = list(self._endpoints.values())
        if task:
            items = [x for x in items if x.task == task]
        items.sort(key=lambda x: (x.priority, -x.weight, x.endpoint_id))
        return items

    def get_endpoint(self, endpoint_id: str) -> Optional[ChatApiEndpoint]:
        """Return a detached endpoint copy so callers cannot mutate pool state."""
        with self._lock:
            endpoint = self._endpoints.get(str(endpoint_id or "").strip())
            return replace(endpoint) if endpoint else None

    def get_default(self, task: str = "chat_llm", tenant_id: str = "*") -> Optional[ChatApiEndpoint]:
        candidates = self.list_candidates(task=task, tenant_id=tenant_id, include_unhealthy=True)
        if not candidates:
            return None
        with self._lock:
            endpoint_id = self._default_endpoint_ids.get(task) or self._default_endpoint_id
            if endpoint_id:
                ep = self._endpoints.get(endpoint_id)
                if ep and ep in candidates:
                    return ep
        return candidates[0]

    def list_candidates(
        self,
        task: str,
        tenant_id: str = "*",
        *,
        include_unhealthy: bool = False,
    ) -> List[ChatApiEndpoint]:
        now = time.time()
        t = str(tenant_id or "*").strip() or "*"
        with self._lock:
            items = [
                ep
                for ep in self._endpoints.values()
                if ep.task == task
                and ep.enabled
                and not ep.frozen
                and (ep.tenant_id in {"*", t})
            ]
            filtered: List[ChatApiEndpoint] = []
            for ep in items:
                h = self._health.get(ep.endpoint_id) or {}
                open_until = float(h.get("circuit_open_until_ts") or 0.0)
                if not include_unhealthy and open_until > now:
                    continue
                filtered.append(ep)
        filtered.sort(key=lambda x: (x.priority, -x.weight, x.endpoint_id))
        return filtered

    def choose_endpoint(
        self,
        task: str,
        tenant_id: str = "*",
        *,
        advance: bool = True,
        exclude_endpoint_ids: Optional[set[str]] = None,
    ) -> Optional[ChatApiEndpoint]:
        candidates = self.list_candidates(task=task, tenant_id=tenant_id)
        excluded = set(exclude_endpoint_ids or set())
        if excluded:
            remaining = [x for x in candidates if x.endpoint_id not in excluded]
            if remaining:
                candidates = remaining
        if not candidates:
            return None
        # Default load balancing is round-robin inside the top priority bucket.
        # Weight is kept as a secondary ordering/config field, but it does not
        # make the default strategy randomly concentrate a whole turn on one key.
        top_priority = candidates[0].priority
        bucket = [x for x in candidates if x.priority == top_priority]
        try:
            latency_aware = bool(get_setting("chat_api_latency_aware_routing_enabled", True))
            latency_ratio = max(1.2, min(float(get_setting("chat_api_latency_routing_ratio", 3.0)), 10.0))
            latency_slack_ms = max(100.0, min(float(get_setting("chat_api_latency_routing_slack_ms", 1500)), 30000.0))
        except Exception:
            latency_aware, latency_ratio, latency_slack_ms = True, 3.0, 1500.0
        if latency_aware and len(bucket) > 1:
            with self._lock:
                known = [
                    float((self._health.get(ep.endpoint_id) or {}).get("latency_ema_ms") or 0.0)
                    for ep in bucket
                ]
            positive = [value for value in known if value > 0]
            if positive:
                best = min(positive)
                latency_ceiling = max(best * latency_ratio, best + latency_slack_ms)
                responsive = [ep for ep, latency in zip(bucket, known) if latency <= 0 or latency <= latency_ceiling]
                if responsive:
                    bucket = responsive
        if len(bucket) == 1:
            return bucket[0]
        key = f"{tenant_id or '*'}:{task}:{top_priority}"
        with self._lock:
            idx = int(self._rr_counters.get(key, 0)) % len(bucket)
            if advance:
                self._rr_counters[key] = idx + 1
        return bucket[idx]

    def upsert_endpoint(self, endpoint: ChatApiEndpoint) -> ChatApiEndpoint:
        with self._lock:
            old = self._endpoints.get(endpoint.endpoint_id)
            endpoint.api_instance_id = str(endpoint.api_instance_id or endpoint.endpoint_id or "").strip()
            explicit_api_key = str(endpoint.api_key or "").strip()
            sibling_with_key = next(
                (
                    e
                    for e in self._endpoints.values()
                    if str(e.api_instance_id or e.endpoint_id) == endpoint.api_instance_id
                    and str(e.api_key or "").strip()
                    and e.endpoint_id != endpoint.endpoint_id
                ),
                None,
            )
            if old and not explicit_api_key and str(old.api_key or "").strip():
                endpoint.api_key = old.api_key
                endpoint.created_at = old.created_at or endpoint.created_at
            elif not explicit_api_key and sibling_with_key is not None:
                endpoint.api_key = sibling_with_key.api_key
            for item in self._endpoints.values():
                if item.endpoint_id == endpoint.endpoint_id:
                    continue
                same_instance = str(item.api_instance_id or item.endpoint_id) == endpoint.api_instance_id
                if same_instance and str(item.task or "") == str(endpoint.task or "") and str(item.model or "") == str(endpoint.model or ""):
                    raise ValueError(
                        f"duplicate model for api instance endpoint: instance={endpoint.api_instance_id}, task={endpoint.task}, model={endpoint.model}"
                    )
            endpoint = self._normalize_endpoint(endpoint)
            if endpoint.enabled and not str(endpoint.api_key or "").strip():
                raise ValueError(
                    "api_key is required for enabled chat API endpoint; "
                    "edit the endpoint and provide a key, or disable it before saving"
                )
            self._endpoints[endpoint.endpoint_id] = endpoint
            if endpoint.api_key:
                for item_id, item in list(self._endpoints.items()):
                    if item_id == endpoint.endpoint_id:
                        continue
                    if str(item.api_instance_id or item.endpoint_id) != endpoint.api_instance_id:
                        continue
                    item.api_key = endpoint.api_key
                    item.provider = endpoint.provider or item.provider
                    item.base_url = endpoint.base_url or item.base_url
                    item.api_instance_name = endpoint.api_instance_name or item.api_instance_name
                    item.api_instance_remark = endpoint.api_instance_remark
                    self._endpoints[item_id] = self._normalize_endpoint(item)
            self._inflight.setdefault(endpoint.endpoint_id, 0)
            self._health.setdefault(
                endpoint.endpoint_id,
                {
                    "success_count": 0.0,
                    "fail_count": 0.0,
                    "consecutive_failures": 0.0,
                    "last_latency_ms": 0.0,
                    "latency_ema_ms": 0.0,
                    "last_error_at_ts": 0.0,
                    "circuit_open_until_ts": 0.0,
                    "half_open_inflight": False,
                    "updated_at_ts": 0.0,
                },
            )
            self._save_state_locked()
        return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self._lock:
            existed = endpoint_id in self._endpoints
            if existed:
                del self._endpoints[endpoint_id]
                self._inflight.pop(endpoint_id, None)
                self._health.pop(endpoint_id, None)
                if self._default_endpoint_id == endpoint_id:
                    self._default_endpoint_id = ""
                for task, eid in list(self._default_endpoint_ids.items()):
                    if eid == endpoint_id:
                        del self._default_endpoint_ids[task]
                self._save_state_locked()
        return existed

    def set_default(self, endpoint_id: str, task: Optional[str] = None) -> str:
        with self._lock:
            if endpoint_id not in self._endpoints:
                raise ValueError("endpoint not found")
            target = self._endpoints[endpoint_id]
            t = task or target.task
            self._default_endpoint_ids[t] = endpoint_id
            if not self._default_endpoint_id:
                self._default_endpoint_id = endpoint_id
            self._save_state_locked()
        return endpoint_id

    def set_frozen(self, endpoint_id: str, frozen: bool) -> Optional[ChatApiEndpoint]:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep:
                return None
            ep.frozen = bool(frozen)
            ep.updated_at = _utc_iso()
            self._endpoints[endpoint_id] = ep
            self._save_state_locked()
            return ep

    def try_acquire_slot(self, endpoint_id: str) -> bool:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep:
                return False
            now = time.time()
            health = self._health.setdefault(endpoint_id, {})
            open_until = float(health.get("circuit_open_until_ts") or 0.0)
            consecutive_failures = int(health.get("consecutive_failures") or 0)
            if open_until > now:
                return False
            is_half_open = open_until > 0 and consecutive_failures >= int(ep.circuit_fail_threshold)
            if is_half_open and bool(health.get("half_open_inflight")):
                return False
            current = int(self._inflight.get(endpoint_id, 0))
            if current >= int(ep.max_concurrency):
                return False
            if is_half_open:
                health["half_open_inflight"] = True
                self._health[endpoint_id] = health
            self._inflight[endpoint_id] = current + 1
            return True

    def release_slot(self, endpoint_id: str) -> None:
        with self._lock:
            current = int(self._inflight.get(endpoint_id, 0))
            remaining = max(0, current - 1)
            self._inflight[endpoint_id] = remaining
            # Cancellation can bypass record_result. Do not leave the sole
            # half-open probe permanently claimed in that case.
            if remaining == 0:
                health = self._health.get(endpoint_id)
                if health and bool(health.get("half_open_inflight")):
                    health["half_open_inflight"] = False
                    self._health[endpoint_id] = health

    def record_result(
        self,
        endpoint_id: str,
        *,
        ok: bool,
        latency_ms: float,
        error: str = "",
        tenant_id: str = "*",
        task: str = "chat_llm",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        input_per_1k: float = 0.0,
        output_per_1k: float = 0.0,
    ) -> None:
        now = time.time()
        with self._lock:
            h = self._health.setdefault(
                endpoint_id,
                {
                    "success_count": 0.0,
                    "fail_count": 0.0,
                    "consecutive_failures": 0.0,
                    "last_latency_ms": 0.0,
                    "latency_ema_ms": 0.0,
                    "last_error_at_ts": 0.0,
                    "circuit_open_until_ts": 0.0,
                    "half_open_inflight": False,
                    "updated_at_ts": 0.0,
                },
            )
            lat = max(0.0, float(latency_ms or 0.0))
            old_ema = float(h.get("latency_ema_ms") or 0.0)
            h["last_latency_ms"] = lat
            h["latency_ema_ms"] = (lat if old_ema <= 0 else (old_ema * 0.8 + lat * 0.2))
            if ok:
                h["success_count"] = float(h.get("success_count") or 0.0) + 1.0
                h["consecutive_failures"] = 0.0
                # close circuit in half-open success path
                h["circuit_open_until_ts"] = 0.0
            else:
                h["fail_count"] = float(h.get("fail_count") or 0.0) + 1.0
                h["consecutive_failures"] = float(h.get("consecutive_failures") or 0.0) + 1.0
                h["last_error_at_ts"] = now
                ep = self._endpoints.get(endpoint_id)
                if ep and int(h["consecutive_failures"]) >= int(ep.circuit_fail_threshold):
                    h["circuit_open_until_ts"] = now + float(ep.circuit_open_seconds)
            h["half_open_inflight"] = False
            h["updated_at_ts"] = now
            self._health[endpoint_id] = h

            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = "|".join(
                [
                    day,
                    str(tenant_id or "*"),
                    str(task or ""),
                    str(endpoint_id or ""),
                    str(model or ""),
                ]
            )
            usage = self._daily_usage.setdefault(
                key,
                {
                    "request_count": 0.0,
                    "success_count": 0.0,
                    "fail_count": 0.0,
                    "input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "cost_input": 0.0,
                    "cost_output": 0.0,
                    "latency_sum_ms": 0.0,
                    "latency_count": 0.0,
                    "latency_p95_approx_ms": 0.0,
                    "updated_at_ts": 0.0,
                },
            )
            usage["request_count"] += 1.0
            usage["success_count"] += 1.0 if ok else 0.0
            usage["fail_count"] += 0.0 if ok else 1.0
            usage["input_tokens"] += float(max(0, input_tokens))
            usage["output_tokens"] += float(max(0, output_tokens))
            usage["cost_input"] += (float(max(0, input_tokens)) / 1000.0) * float(max(0.0, input_per_1k))
            usage["cost_output"] += (float(max(0, output_tokens)) / 1000.0) * float(max(0.0, output_per_1k))
            usage["latency_sum_ms"] += lat
            usage["latency_count"] += 1.0
            # lightweight approx; not true percentile but monotonic indicator.
            usage["latency_p95_approx_ms"] = max(float(usage.get("latency_p95_approx_ms") or 0.0) * 0.98, lat)
            usage["updated_at_ts"] = now
            self._daily_usage[key] = usage
            # lightweight online evaluation; cooldown in rule prevents noisy storm.
            try:
                self.evaluate_alerts()
            except Exception:
                pass
            self._save_state_locked()

    def endpoint_health(self, task: Optional[str] = None) -> List[Dict]:
        now = time.time()
        with self._lock:
            items: List[Dict] = []
            endpoints = self.list_endpoints(task=task)
            for ep in endpoints:
                h = dict(self._health.get(ep.endpoint_id) or {})
                open_until = float(h.get("circuit_open_until_ts") or 0.0)
                consecutive_failures = int(h.get("consecutive_failures") or 0)
                half_open = (
                    open_until > 0
                    and open_until <= now
                    and consecutive_failures >= int(ep.circuit_fail_threshold)
                )
                items.append(
                    {
                        "endpoint_id": ep.endpoint_id,
                        "name": ep.name,
                        "task": ep.task,
                        "tenant_id": ep.tenant_id,
                        "enabled": ep.enabled,
                        "frozen": ep.frozen,
                        "priority": ep.priority,
                        "weight": ep.weight,
                        "max_concurrency": ep.max_concurrency,
                        "inflight": int(self._inflight.get(ep.endpoint_id, 0)),
                        "circuit_state": ("OPEN" if open_until > now else ("HALF_OPEN" if half_open else "CLOSED")),
                        "circuit_open_until": (
                            datetime.fromtimestamp(open_until, tz=timezone.utc).isoformat()
                            if open_until > now
                            else None
                        ),
                        "success_count": int(h.get("success_count") or 0),
                        "fail_count": int(h.get("fail_count") or 0),
                        "consecutive_failures": int(h.get("consecutive_failures") or 0),
                        "last_latency_ms": round(float(h.get("last_latency_ms") or 0.0), 2),
                        "latency_ema_ms": round(float(h.get("latency_ema_ms") or 0.0), 2),
                        "updated_at": (
                            datetime.fromtimestamp(float(h.get("updated_at_ts") or 0.0), tz=timezone.utc).isoformat()
                            if float(h.get("updated_at_ts") or 0.0) > 0
                            else None
                        ),
                    }
                )
        return items

    def usage_daily(
        self,
        *,
        day_from: str = "",
        day_to: str = "",
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        model: str = "",
        limit: int = 200,
    ) -> List[Dict]:
        with self._lock:
            out: List[Dict] = []
            for key, usage in self._daily_usage.items():
                day, t, tk, eid, m = key.split("|", 4)
                if day_from and day < day_from:
                    continue
                if day_to and day > day_to:
                    continue
                if tenant_id and t != tenant_id:
                    continue
                if task and tk != task:
                    continue
                if endpoint_id and eid != endpoint_id:
                    continue
                if model and m != model:
                    continue
                req = max(1.0, float(usage.get("request_count") or 1.0))
                lat_count = max(1.0, float(usage.get("latency_count") or 1.0))
                cost_input = float(usage.get("cost_input") or 0.0)
                cost_output = float(usage.get("cost_output") or 0.0)
                out.append(
                    {
                        "date": day,
                        "tenant_id": t,
                        "task": tk,
                        "endpoint_id": eid,
                        "model": m,
                        "request_count": int(usage.get("request_count") or 0),
                        "success_count": int(usage.get("success_count") or 0),
                        "fail_count": int(usage.get("fail_count") or 0),
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                        "cost_input": round(cost_input, 6),
                        "cost_output": round(cost_output, 6),
                        "cost_total": round(cost_input + cost_output, 6),
                        "latency_avg_ms": round(float(usage.get("latency_sum_ms") or 0.0) / lat_count, 2),
                        "latency_p95_approx_ms": round(float(usage.get("latency_p95_approx_ms") or 0.0), 2),
                        "success_rate": round(float(usage.get("success_count") or 0.0) / req, 4),
                    }
                )
            out.sort(key=lambda x: (x["date"], x["request_count"]), reverse=True)
            return out[: max(1, min(2000, int(limit)))]

    def usage_grouped(
        self,
        *,
        group_by: str = "tenant,task,model,endpoint_id",
        day_from: str = "",
        day_to: str = "",
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        model: str = "",
        sort_by: str = "request_count",
        sort_dir: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> Dict:
        allowed_fields = {"date", "tenant", "task", "endpoint_id", "model"}
        group_fields = [x.strip() for x in str(group_by or "").split(",") if x.strip()]
        group_fields = [x for x in group_fields if x in allowed_fields]
        if not group_fields:
            group_fields = ["tenant", "task", "model", "endpoint_id"]
        rows = self.usage_daily(
            day_from=day_from,
            day_to=day_to,
            tenant_id=tenant_id,
            task=task,
            endpoint_id=endpoint_id,
            model=model,
            limit=2000,
        )
        agg: Dict[str, Dict] = {}
        for row in rows:
            key_parts = []
            for f in group_fields:
                if f == "tenant":
                    key_parts.append(str(row.get("tenant_id") or ""))
                else:
                    key_parts.append(str(row.get(f) or ""))
            gkey = "|".join(key_parts)
            item = agg.setdefault(
                gkey,
                {
                    "request_count": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_total": 0.0,
                    "cost_input": 0.0,
                    "cost_output": 0.0,
                    "latency_p95_approx_ms": 0.0,
                },
            )
            item["request_count"] += int(row.get("request_count") or 0)
            item["success_count"] += int(row.get("success_count") or 0)
            item["fail_count"] += int(row.get("fail_count") or 0)
            item["input_tokens"] += int(row.get("input_tokens") or 0)
            item["output_tokens"] += int(row.get("output_tokens") or 0)
            item["cost_total"] += float(row.get("cost_total") or 0.0)
            item["cost_input"] += float(row.get("cost_input") or 0.0)
            item["cost_output"] += float(row.get("cost_output") or 0.0)
            item["latency_p95_approx_ms"] = max(
                float(item.get("latency_p95_approx_ms") or 0.0),
                float(row.get("latency_p95_approx_ms") or 0.0),
            )
            for f in group_fields:
                if f == "tenant":
                    item["tenant_id"] = str(row.get("tenant_id") or "")
                else:
                    item[f] = row.get(f)
        out = list(agg.values())
        for one in out:
            req = max(1, int(one.get("request_count") or 0))
            one["success_rate"] = round(float(one.get("success_count") or 0) / req, 4)
            one["error_rate"] = round(float(one.get("fail_count") or 0) / req, 4)
            one["cost_total"] = round(float(one.get("cost_total") or 0.0), 6)
            one["cost_input"] = round(float(one.get("cost_input") or 0.0), 6)
            one["cost_output"] = round(float(one.get("cost_output") or 0.0), 6)
            one["latency_p95_approx_ms"] = round(float(one.get("latency_p95_approx_ms") or 0.0), 2)

        sort_key = str(sort_by or "request_count")
        reverse = str(sort_dir or "desc").lower() != "asc"
        out.sort(key=lambda x: x.get(sort_key, 0), reverse=reverse)
        p = max(1, int(page or 1))
        ps = max(1, min(500, int(page_size or 50)))
        total = len(out)
        start = (p - 1) * ps
        items = out[start : start + ps]
        return {
            "total": total,
            "page": p,
            "page_size": ps,
            "group_by": group_fields,
            "sort_by": sort_key,
            "sort_dir": "desc" if reverse else "asc",
            "items": items,
        }

    def usage_trends(
        self,
        *,
        day_from: str = "",
        day_to: str = "",
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        model: str = "",
        limit_days: int = 60,
    ) -> List[Dict]:
        rows = self.usage_daily(
            day_from=day_from,
            day_to=day_to,
            tenant_id=tenant_id,
            task=task,
            endpoint_id=endpoint_id,
            model=model,
            limit=5000,
        )
        by_day: Dict[str, Dict] = {}
        for row in rows:
            day = str(row.get("date") or "")
            if not day:
                continue
            item = by_day.setdefault(
                day,
                {
                    "date": day,
                    "request_count": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_total": 0.0,
                    "latency_p95_approx_ms": 0.0,
                },
            )
            item["request_count"] += int(row.get("request_count") or 0)
            item["success_count"] += int(row.get("success_count") or 0)
            item["fail_count"] += int(row.get("fail_count") or 0)
            item["input_tokens"] += int(row.get("input_tokens") or 0)
            item["output_tokens"] += int(row.get("output_tokens") or 0)
            item["cost_total"] += float(row.get("cost_total") or 0.0)
            item["latency_p95_approx_ms"] = max(
                float(item.get("latency_p95_approx_ms") or 0.0),
                float(row.get("latency_p95_approx_ms") or 0.0),
            )
        out = list(by_day.values())
        for one in out:
            req = max(1, int(one.get("request_count") or 0))
            one["success_rate"] = round(float(one.get("success_count") or 0) / req, 4)
            one["error_rate"] = round(float(one.get("fail_count") or 0) / req, 4)
            one["cost_total"] = round(float(one.get("cost_total") or 0.0), 6)
            one["latency_p95_approx_ms"] = round(float(one.get("latency_p95_approx_ms") or 0.0), 2)
        out.sort(key=lambda x: str(x.get("date") or ""))
        return out[-max(1, min(365, int(limit_days))):]

    def list_alert_rules(self) -> List[Dict]:
        with self._lock:
            items = [r for r in self._alert_rules.values()]
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return [x.__dict__.copy() for x in items]

    def alert_rule_stats(self, *, limit: int = 200) -> List[Dict]:
        lim = max(1, min(2000, int(limit or 200)))
        with self._lock:
            rules = {k: v for k, v in self._alert_rules.items()}
            events = list(self._alert_events)
        by_rule: Dict[str, Dict] = {}
        for rid, rule in rules.items():
            by_rule[rid] = {
                "rule_id": rid,
                "rule_name": rule.name,
                "enabled": bool(rule.enabled),
                "severity": rule.severity,
                "metric": rule.metric,
                "scope": rule.scope,
                "trigger_count": 0,
                "unacked_count": 0,
                "last_trigger_at": None,
                "last_value": None,
                "threshold": float(rule.threshold),
                "comparison": rule.comparison,
            }
        for evt in events:
            rid = str(evt.rule_id or "")
            row = by_rule.setdefault(
                rid,
                {
                    "rule_id": rid,
                    "rule_name": str(evt.rule_name or ""),
                    "enabled": False,
                    "severity": str(evt.severity or "warning"),
                    "metric": str(evt.metric or ""),
                    "scope": str(evt.scope or ""),
                    "trigger_count": 0,
                    "unacked_count": 0,
                    "last_trigger_at": None,
                    "last_value": None,
                    "threshold": float(evt.threshold or 0.0),
                    "comparison": "",
                },
            )
            row["trigger_count"] += 1
            if not bool(evt.acked):
                row["unacked_count"] += 1
            created_at = str(evt.created_at or "")
            if not row["last_trigger_at"] or created_at > str(row["last_trigger_at"]):
                row["last_trigger_at"] = created_at
                row["last_value"] = float(evt.value or 0.0)
        out = list(by_rule.values())
        out.sort(key=lambda x: (int(x.get("unacked_count") or 0), int(x.get("trigger_count") or 0)), reverse=True)
        return out[:lim]

    def upsert_alert_rule(self, rule: ChatApiAlertRule) -> Dict:
        with self._lock:
            normalized = self._normalize_rule(rule)
            self._alert_rules[normalized.rule_id] = normalized
            self._save_state_locked()
            return normalized.__dict__.copy()

    def delete_alert_rule(self, rule_id: str) -> bool:
        rid = str(rule_id or "").strip()
        with self._lock:
            existed = rid in self._alert_rules
            if existed:
                self._alert_rules.pop(rid, None)
                self._last_alert_fired_at.pop(rid, None)
                self._save_state_locked()
        return existed

    def list_alert_events(self, limit: int = 200) -> List[Dict]:
        with self._lock:
            arr = list(self._alert_events)
        arr.sort(key=lambda x: x.created_at, reverse=True)
        return [x.__dict__.copy() for x in arr[: max(1, min(2000, int(limit)))]]

    def list_alert_events_paged(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        rule_id: str = "",
        severity: str = "",
        acked: Optional[bool] = None,
        tenant_id: str = "",
        day_from: str = "",
        day_to: str = "",
    ) -> Dict:
        with self._lock:
            arr = list(self._alert_events)
        arr.sort(key=lambda x: x.created_at, reverse=True)
        filtered: List[Dict] = []
        for one in arr:
            if rule_id and str(one.rule_id or "") != rule_id:
                continue
            if severity and str(one.severity or "") != severity:
                continue
            if acked is not None and bool(one.acked) != bool(acked):
                continue
            if tenant_id and str(one.tenant_id or "") != tenant_id:
                continue
            created_day = str(one.created_at or "")[:10]
            if day_from and created_day and created_day < day_from:
                continue
            if day_to and created_day and created_day > day_to:
                continue
            filtered.append(one.__dict__.copy())
        p = max(1, int(page or 1))
        ps = max(1, min(500, int(page_size or 50)))
        total = len(filtered)
        start = (p - 1) * ps
        items = filtered[start : start + ps]
        return {
            "total": total,
            "page": p,
            "page_size": ps,
            "items": items,
        }

    def ack_alert_event(self, event_id: str, actor: str) -> bool:
        eid = str(event_id or "").strip()
        if not eid:
            return False
        with self._lock:
            changed = False
            for i, one in enumerate(self._alert_events):
                if str(one.event_id or "") != eid:
                    continue
                if one.acked:
                    return True
                one.acked = True
                one.acked_by = str(actor or "")
                one.acked_at = _utc_iso()
                self._alert_events[i] = one
                changed = True
                break
            if changed:
                self._save_state_locked()
            return changed

    def _emit_alert_event_locked(
        self,
        rule: ChatApiAlertRule,
        *,
        value: float,
        threshold: float,
        message: str,
    ) -> None:
        now_ts = time.time()
        last = float(self._last_alert_fired_at.get(rule.rule_id) or 0.0)
        if now_ts - last < float(rule.cooldown_seconds):
            return
        self._last_alert_fired_at[rule.rule_id] = now_ts
        evt = ChatApiAlertEvent(
            event_id=str(uuid.uuid4()),
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=rule.severity,
            metric=rule.metric,
            scope=rule.scope,
            message=message,
            value=float(value),
            threshold=float(threshold),
            tenant_id=rule.tenant_id,
            task=rule.task,
            endpoint_id=rule.endpoint_id,
            model=rule.model,
            created_at=_utc_iso(),
        )
        self._alert_events.append(evt)
        if len(self._alert_events) > 5000:
            self._alert_events = self._alert_events[-5000:]
        self._save_state_locked()

    def add_call_log(self, log: ChatApiCallLog) -> Dict:
        with self._lock:
            self._call_logs.append(log)
            if len(self._call_logs) > 5000:
                self._call_logs = self._call_logs[-5000:]
            self._save_state_locked()
            return log.__dict__.copy()

    def list_call_logs(
        self,
        *,
        limit: int = 200,
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        ok: Optional[bool] = None,
    ) -> List[Dict]:
        lim = max(1, min(2000, int(limit)))
        with self._lock:
            arr = list(self._call_logs)
        arr.sort(key=lambda x: x.created_at, reverse=True)
        out: List[Dict] = []
        for one in arr:
            if tenant_id and str(one.tenant_id or "") != tenant_id:
                continue
            if task and str(one.task or "") != task:
                continue
            if endpoint_id and str(one.endpoint_id or "") != endpoint_id:
                continue
            if ok is not None and bool(one.ok) != bool(ok):
                continue
            out.append(one.__dict__.copy())
            if len(out) >= lim:
                break
        return out

    def call_logs_grouped(
        self,
        *,
        group_by: str = "tenant_id,task,endpoint_id,model",
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        ok: Optional[bool] = None,
        limit: int = 200,
        sort_by: str = "request_count",
        sort_dir: str = "desc",
    ) -> List[Dict]:
        fields_allowed = {"tenant_id", "task", "endpoint_id", "model", "base_url", "path"}
        groups = [x.strip() for x in str(group_by or "").split(",") if x.strip() in fields_allowed]
        if not groups:
            groups = ["tenant_id", "task", "endpoint_id", "model"]
        logs = self.list_call_logs(
            limit=5000,
            tenant_id=tenant_id,
            task=task,
            endpoint_id=endpoint_id,
            ok=ok,
        )
        agg: Dict[str, Dict] = {}
        for row in logs:
            key = "|".join(str(row.get(g) or "") for g in groups)
            one = agg.setdefault(
                key,
                {
                    "request_count": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "latency_sum_ms": 0.0,
                    "latency_p95_approx_ms": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_total": 0.0,
                    "last_error": "",
                    "last_error_at": "",
                },
            )
            for g in groups:
                one[g] = row.get(g)
            one["request_count"] += 1
            if bool(row.get("ok")):
                one["success_count"] += 1
            else:
                one["fail_count"] += 1
                err = str(row.get("error") or "")
                if err:
                    one["last_error"] = err
                    one["last_error_at"] = str(row.get("created_at") or "")
            lat = float(row.get("latency_ms") or 0.0)
            one["latency_sum_ms"] += lat
            one["latency_p95_approx_ms"] = max(float(one.get("latency_p95_approx_ms") or 0.0) * 0.98, lat)
            one["input_tokens"] += int(row.get("input_tokens") or 0)
            one["output_tokens"] += int(row.get("output_tokens") or 0)
            one["cost_total"] += float(row.get("cost_total") or 0.0)
        out = list(agg.values())
        for one in out:
            req = max(1, int(one.get("request_count") or 0))
            one["success_rate"] = round(float(one.get("success_count") or 0) / req, 4)
            one["error_rate"] = round(float(one.get("fail_count") or 0) / req, 4)
            one["latency_avg_ms"] = round(float(one.get("latency_sum_ms") or 0.0) / req, 2)
            one["latency_p95_approx_ms"] = round(float(one.get("latency_p95_approx_ms") or 0.0), 2)
            one["cost_total"] = round(float(one.get("cost_total") or 0.0), 6)
        s_key = str(sort_by or "request_count")
        reverse = str(sort_dir or "desc").lower() != "asc"
        out.sort(key=lambda x: x.get(s_key, 0), reverse=reverse)
        return out[: max(1, min(2000, int(limit or 200)))]

    @staticmethod
    def _compare(value: float, threshold: float, comparison: str) -> bool:
        if comparison == "lte":
            return float(value) <= float(threshold)
        return float(value) >= float(threshold)

    def evaluate_alerts(self) -> Dict:
        with self._lock:
            rules = list(self._alert_rules.values())
            usage_snapshot = dict(self._daily_usage)
            now = datetime.now(timezone.utc)
            days = [
                (now.date()).isoformat(),
                *((now.date()).fromordinal(now.date().toordinal() - i).isoformat() for i in range(1, 30)),
            ]
            fired = 0
            checked = 0
            for rule in rules:
                if not rule.enabled:
                    continue
                checked += 1
                allowed_days = set(days[: max(1, int(rule.window_days))])
                rows: List[Dict] = []
                for key, usage in usage_snapshot.items():
                    day, t, tk, eid, m = key.split("|", 4)
                    if day not in allowed_days:
                        continue
                    if rule.scope in {"tenant", "task", "endpoint", "model"} and rule.tenant_id not in {"*", t}:
                        continue
                    if rule.scope in {"task", "endpoint", "model"} and rule.task and rule.task != tk:
                        continue
                    if rule.scope in {"endpoint", "model"} and rule.endpoint_id and rule.endpoint_id != eid:
                        continue
                    if rule.scope == "model" and rule.model and rule.model != m:
                        continue
                    req = float(usage.get("request_count") or 0.0)
                    succ = float(usage.get("success_count") or 0.0)
                    fail = float(usage.get("fail_count") or 0.0)
                    in_tok = float(usage.get("input_tokens") or 0.0)
                    out_tok = float(usage.get("output_tokens") or 0.0)
                    c_in = float(usage.get("cost_input") or 0.0)
                    c_out = float(usage.get("cost_output") or 0.0)
                    lat95 = float(usage.get("latency_p95_approx_ms") or 0.0)
                    rows.append(
                        {
                            "request_count": req,
                            "success_count": succ,
                            "fail_count": fail,
                            "total_tokens": in_tok + out_tok,
                            "cost_total": c_in + c_out,
                            "error_rate": (fail / req) if req > 0 else 0.0,
                            "success_rate": (succ / req) if req > 0 else 0.0,
                            "latency_p95_approx_ms": lat95,
                        }
                    )
                if not rows:
                    continue
                metric_map = {
                    "daily_cost_total": sum(x["cost_total"] for x in rows),
                    "daily_total_tokens": sum(x["total_tokens"] for x in rows),
                    "error_rate": sum(x["error_rate"] for x in rows) / max(1, len(rows)),
                    "success_rate": sum(x["success_rate"] for x in rows) / max(1, len(rows)),
                    "latency_p95_approx_ms": max(x["latency_p95_approx_ms"] for x in rows),
                }
                value = float(metric_map.get(rule.metric, 0.0))
                hit = self._compare(value, float(rule.threshold), rule.comparison)
                if hit:
                    fired += 1
                    self._emit_alert_event_locked(
                        rule,
                        value=value,
                        threshold=float(rule.threshold),
                        message=(
                            f"Rule '{rule.name}' triggered: metric={rule.metric}, "
                            f"value={value:.6f}, threshold={rule.threshold:.6f}, cmp={rule.comparison}."
                        ),
                    )
            return {
                "checked": checked,
                "fired": fired,
                "event_count": len(self._alert_events),
            }

    def overview(self) -> Dict:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        with self._lock:
            endpoints_total = len(self._endpoints)
            endpoints_enabled = sum(1 for x in self._endpoints.values() if x.enabled and not x.frozen)
            open_circuit = 0
            now_ts = time.time()
            for h in self._health.values():
                if float(h.get("circuit_open_until_ts") or 0.0) > now_ts:
                    open_circuit += 1
            today_rows = []
            for key, usage in self._daily_usage.items():
                day, _, _, _, _ = key.split("|", 4)
                if day == today:
                    today_rows.append(usage)
            req = sum(float(x.get("request_count") or 0.0) for x in today_rows)
            succ = sum(float(x.get("success_count") or 0.0) for x in today_rows)
            fail = sum(float(x.get("fail_count") or 0.0) for x in today_rows)
            in_tok = sum(float(x.get("input_tokens") or 0.0) for x in today_rows)
            out_tok = sum(float(x.get("output_tokens") or 0.0) for x in today_rows)
            cost = sum(float(x.get("cost_input") or 0.0) + float(x.get("cost_output") or 0.0) for x in today_rows)
            latency_p95 = max([float(x.get("latency_p95_approx_ms") or 0.0) for x in today_rows] or [0.0])
            unacked = sum(1 for x in self._alert_events if not bool(x.acked))
            return {
                "date": today,
                "endpoints_total": endpoints_total,
                "endpoints_enabled": endpoints_enabled,
                "circuit_open_count": open_circuit,
                "today_request_count": int(req),
                "today_success_count": int(succ),
                "today_fail_count": int(fail),
                "today_success_rate": round((succ / req) if req > 0 else 0.0, 4),
                "today_input_tokens": int(in_tok),
                "today_output_tokens": int(out_tok),
                "today_total_cost": round(cost, 6),
                "today_latency_p95_approx_ms": round(latency_p95, 2),
                "alert_rules_count": len(self._alert_rules),
                "alert_events_count": len(self._alert_events),
                "alert_unacked_count": unacked,
            }

    def insights(self, *, day_from: str = "", day_to: str = "", limit: int = 10) -> Dict:
        rows = self.usage_daily(day_from=day_from, day_to=day_to, limit=2000)
        lim = max(1, min(100, int(limit or 10)))

        by_endpoint: Dict[str, Dict] = {}
        by_model: Dict[str, Dict] = {}
        by_tenant: Dict[str, Dict] = {}
        for row in rows:
            eid = str(row.get("endpoint_id") or "")
            mdl = str(row.get("model") or "")
            tnt = str(row.get("tenant_id") or "")
            for key, bucket, label in [
                (eid, by_endpoint, "endpoint_id"),
                (mdl, by_model, "model"),
                (tnt, by_tenant, "tenant_id"),
            ]:
                item = bucket.setdefault(
                    key,
                    {
                        label: key,
                        "request_count": 0,
                        "success_count": 0,
                        "fail_count": 0,
                        "cost_total": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                )
                item["request_count"] += int(row.get("request_count") or 0)
                item["success_count"] += int(row.get("success_count") or 0)
                item["fail_count"] += int(row.get("fail_count") or 0)
                item["cost_total"] += float(row.get("cost_total") or 0.0)
                item["input_tokens"] += int(row.get("input_tokens") or 0)
                item["output_tokens"] += int(row.get("output_tokens") or 0)

        def finalize(arr: List[Dict]) -> List[Dict]:
            out: List[Dict] = []
            for one in arr:
                req = max(1, int(one.get("request_count") or 0))
                one["error_rate"] = round(float(one.get("fail_count") or 0) / req, 4)
                one["success_rate"] = round(float(one.get("success_count") or 0) / req, 4)
                one["cost_total"] = round(float(one.get("cost_total") or 0.0), 6)
                out.append(one)
            return out

        endpoint_items = finalize(list(by_endpoint.values()))
        model_items = finalize(list(by_model.values()))
        tenant_items = finalize(list(by_tenant.values()))
        endpoint_items.sort(key=lambda x: (x.get("error_rate", 0), x.get("fail_count", 0)), reverse=True)
        model_items.sort(key=lambda x: x.get("cost_total", 0.0), reverse=True)
        tenant_items.sort(key=lambda x: x.get("request_count", 0), reverse=True)
        return {
            "top_failure_endpoints": endpoint_items[:lim],
            "top_cost_models": model_items[:lim],
            "top_request_tenants": tenant_items[:lim],
        }

    def dashboard_pack(
        self,
        *,
        day_from: str = "",
        day_to: str = "",
        tenant_id: str = "",
        task: str = "",
        endpoint_id: str = "",
        model: str = "",
    ) -> Dict:
        overview = self.overview()
        trends = self.usage_trends(
            day_from=day_from,
            day_to=day_to,
            tenant_id=tenant_id,
            task=task,
            endpoint_id=endpoint_id,
            model=model,
            limit_days=30,
        )
        insights = self.insights(day_from=day_from, day_to=day_to, limit=10)
        health = self.endpoint_health(task=task or None)
        health_sorted = sorted(
            health,
            key=lambda x: (
                int(x.get("circuit_state") == "OPEN"),
                float(x.get("latency_ema_ms") or 0.0),
                int(x.get("fail_count") or 0),
            ),
            reverse=True,
        )
        alerts = self.list_alert_events(limit=20)
        return {
            "overview": overview,
            "trends": trends,
            "insights": insights,
            "health_top": health_sorted[:10],
            "recent_alerts": alerts,
        }


chat_api_pool_manager = ChatApiPoolManager()
try:
    chat_api_pool_manager.load_state()
except Exception:
    pass
