import json
import os
import time
from datetime import datetime, timezone
import hashlib
from threading import Lock

from pydantic import BaseModel, Field

from models.api_pool_store import ApiPoolMongoStore


class ApiEndpoint(BaseModel):
    endpoint_id: str
    name: str
    provider: str
    base_url: str
    model: str
    task: str = "llm"  # llm | embedding
    api_key: str = ""
    api_instance_id: str = ""
    api_instance_name: str = ""
    api_instance_remark: str = ""
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
    key_fingerprint: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    last_rotated_at: str | None = None
    expires_at: str | None = None
    rotation_state: str = "ACTIVE"
    previous_fingerprint: str = ""
    rotation_note: str = ""


class ApiPoolSnapshot(BaseModel):
    default_endpoint_id: str | None = None
    default_endpoint_ids: dict[str, str] = Field(default_factory=dict)
    endpoints: list[ApiEndpoint] = Field(default_factory=list)


class ApiPoolManager:
    def __init__(self, namespace: str = "api_pool") -> None:
        self._lock = Lock()
        self._snapshot = ApiPoolSnapshot()
        self._store = ApiPoolMongoStore(namespace=namespace)
        self._rr_counters: dict[str, int] = {}
        self._refresh_seconds = _refresh_interval_seconds()
        self._last_refresh_monotonic = 0.0

    def bootstrap(self, file_path: str = "") -> ApiPoolSnapshot:
        """Load persisted API pool state from the database only."""
        with self._lock:
            try:
                if self._refresh_from_store_locked():
                    return self._snapshot
            except Exception:
                pass

            self._snapshot = ApiPoolSnapshot()
            try:
                self._persist_snapshot_locked()
            except Exception:
                pass
            self._last_refresh_monotonic = time.monotonic()
            return self._snapshot

    def refresh_if_stale(self, force: bool = False) -> bool:
        """Refresh the process-local snapshot without discarding the last valid state on failure."""
        now = time.monotonic()
        if not force and now - self._last_refresh_monotonic < self._refresh_seconds:
            return False
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_refresh_monotonic < self._refresh_seconds:
                return False
            try:
                return self._refresh_from_store_locked()
            except Exception:
                self._last_refresh_monotonic = now
                return False

    def load_from_file(self, file_path: str) -> ApiPoolSnapshot:
        raise RuntimeError("API pool file loading is disabled; use API management database")

    def reset_from_file(self, file_path: str) -> ApiPoolSnapshot:
        raise RuntimeError("API pool file loading is disabled; use API management database")

    def list_endpoints(self, tenant_id: str | None = None, task: str | None = None) -> list[ApiEndpoint]:
        self.refresh_if_stale()
        with self._lock:
            if tenant_id is None:
                data = list(self._snapshot.endpoints)
            else:
                data = [
                e
                for e in self._snapshot.endpoints
                if e.tenant_id in ("*", tenant_id)
                ]
            if task:
                data = [e for e in data if e.task == task]
            return data

    def upsert_endpoint(self, endpoint: ApiEndpoint) -> ApiEndpoint:
        self.refresh_if_stale(force=True)
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            endpoint.tenant_id = (endpoint.tenant_id or "*").strip() or "*"
            endpoint.api_instance_id = (endpoint.api_instance_id or endpoint.endpoint_id or "").strip()
            endpoint.api_instance_name = (endpoint.api_instance_name or endpoint.name or endpoint.api_instance_id).strip()
            endpoint.api_instance_remark = (endpoint.api_instance_remark or "").strip()
            endpoint.priority = int(max(1, endpoint.priority or 100))
            endpoint.weight = int(max(1, endpoint.weight or 100))
            endpoint.max_concurrency = int(max(1, endpoint.max_concurrency or 8))
            endpoint.timeout_seconds = int(max(5, endpoint.timeout_seconds or 120))
            endpoint.retry_max = int(max(0, endpoint.retry_max or 2))
            endpoint.circuit_fail_threshold = int(max(1, endpoint.circuit_fail_threshold or 5))
            endpoint.circuit_open_seconds = int(max(5, endpoint.circuit_open_seconds or 30))
            for item in self._snapshot.endpoints:
                if item.endpoint_id == endpoint.endpoint_id:
                    continue
                same_instance = (item.api_instance_id or item.endpoint_id) == endpoint.api_instance_id
                same_task = str(item.task or "") == str(endpoint.task or "")
                same_model = str(item.model or "") == str(endpoint.model or "")
                if same_instance and same_task and same_model:
                    raise ValueError(
                        f"duplicate model for api instance endpoint: instance={endpoint.api_instance_id}, task={endpoint.task}, model={endpoint.model}"
                    )
            replaced = False
            for idx, item in enumerate(self._snapshot.endpoints):
                if item.endpoint_id == endpoint.endpoint_id:
                    old_key = item.api_key or ""
                    next_key = endpoint.api_key or ""
                    if not next_key:
                        next_key = old_key
                    endpoint.api_key = next_key
                    endpoint.created_at = endpoint.created_at or item.created_at or now
                    endpoint.updated_at = now
                    endpoint.key_fingerprint = _fingerprint_api_key(next_key)
                    endpoint.rotation_state = (endpoint.rotation_state or item.rotation_state or "ACTIVE").upper()
                    if old_key and next_key and old_key != next_key:
                        endpoint.previous_fingerprint = _fingerprint_api_key(old_key)
                        endpoint.last_rotated_at = now
                    else:
                        endpoint.previous_fingerprint = endpoint.previous_fingerprint or item.previous_fingerprint
                        endpoint.last_rotated_at = endpoint.last_rotated_at or item.last_rotated_at
                    if not endpoint.expires_at:
                        endpoint.expires_at = item.expires_at
                    if not endpoint.rotation_note:
                        endpoint.rotation_note = item.rotation_note
                    replaced = True
                    self._snapshot.endpoints[idx] = endpoint
                    break
            if not replaced:
                if not endpoint.api_key:
                    sibling = next(
                        (
                            e
                            for e in self._snapshot.endpoints
                            if (e.api_instance_id or e.endpoint_id) == endpoint.api_instance_id and (e.api_key or "")
                        ),
                        None,
                    )
                    if sibling is not None:
                        endpoint.api_key = sibling.api_key
                endpoint.created_at = endpoint.created_at or now
                endpoint.updated_at = now
                endpoint.key_fingerprint = _fingerprint_api_key(endpoint.api_key or "")
                endpoint.rotation_state = (endpoint.rotation_state or "ACTIVE").upper()
                self._snapshot.endpoints.append(endpoint)
            if self._snapshot.default_endpoint_id is None:
                self._snapshot.default_endpoint_id = endpoint.endpoint_id
            if endpoint.task not in self._snapshot.default_endpoint_ids:
                self._snapshot.default_endpoint_ids[endpoint.task] = endpoint.endpoint_id
            try:
                self._store.upsert_endpoint(endpoint.model_dump())
                self._store.update_defaults(
                    self._snapshot.default_endpoint_id,
                    self._snapshot.default_endpoint_ids,
                )
            except Exception:
                pass
            self._last_refresh_monotonic = time.monotonic()
            return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
        self.refresh_if_stale(force=True)
        with self._lock:
            before = len(self._snapshot.endpoints)
            self._snapshot.endpoints = [
                e for e in self._snapshot.endpoints if e.endpoint_id != endpoint_id
            ]
            deleted = len(self._snapshot.endpoints) < before
            if deleted and self._snapshot.default_endpoint_id == endpoint_id:
                self._snapshot.default_endpoint_id = (
                    self._snapshot.endpoints[0].endpoint_id if self._snapshot.endpoints else None
                )
            if deleted:
                self._snapshot.default_endpoint_ids = {
                    task: eid
                    for task, eid in self._snapshot.default_endpoint_ids.items()
                    if eid != endpoint_id
                }
                try:
                    self._store.delete_endpoint(endpoint_id)
                    self._store.update_defaults(
                        self._snapshot.default_endpoint_id,
                        self._snapshot.default_endpoint_ids,
                    )
                except Exception:
                    pass
            self._last_refresh_monotonic = time.monotonic()
            return deleted

    def set_default(self, endpoint_id: str, task: str | None = None) -> str:
        self.refresh_if_stale(force=True)
        with self._lock:
            endpoint = next((e for e in self._snapshot.endpoints if e.endpoint_id == endpoint_id), None)
            if endpoint is None:
                raise ValueError(f"Endpoint not found: {endpoint_id}")
            self._snapshot.default_endpoint_id = endpoint_id
            target_task = task or endpoint.task
            self._snapshot.default_endpoint_ids[target_task] = endpoint_id
            try:
                self._store.update_defaults(
                    self._snapshot.default_endpoint_id,
                    self._snapshot.default_endpoint_ids,
                )
            except Exception:
                pass
            self._last_refresh_monotonic = time.monotonic()
            return endpoint_id

    def get_default_for_tenant(self, tenant_id: str, task: str = "llm") -> ApiEndpoint | None:
        self.refresh_if_stale()
        with self._lock:
            candidates = [
                e
                for e in self._snapshot.endpoints
                if e.task == task and e.enabled and not e.frozen and e.tenant_id in ("*", tenant_id)
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda x: (x.priority, -x.weight, x.endpoint_id))
            top_priority = candidates[0].priority
            bucket = [e for e in candidates if e.priority == top_priority]
            if len(bucket) == 1:
                return bucket[0]
            key = f"{tenant_id}:{task}:{top_priority}"
            start = self._rr_counters.get(key, 0) % len(bucket)
            self._rr_counters[key] = start + 1
            return bucket[start]

    def list_candidates_for_tenant(self, tenant_id: str, task: str = "llm") -> list[ApiEndpoint]:
        self.refresh_if_stale()
        with self._lock:
            candidates = [
                e
                for e in self._snapshot.endpoints
                if e.task == task and e.enabled and not e.frozen and e.tenant_id in ("*", tenant_id)
            ]
            if not candidates:
                return []
            candidates.sort(key=lambda x: (x.priority, -x.weight, x.endpoint_id))
            key = f"{tenant_id}:{task}:candidates"
            start = self._rr_counters.get(key, 0) % len(candidates)
            self._rr_counters[key] = start + 1
            return candidates[start:] + candidates[:start]

    def list_instances(self, tenant_id: str | None = None, task: str | None = None) -> list[dict]:
        endpoints = self.list_endpoints(tenant_id=tenant_id, task=task)
        groups: dict[str, dict] = {}
        for endpoint in endpoints:
            instance_id = (endpoint.api_instance_id or endpoint.endpoint_id or "").strip()
            if not instance_id:
                instance_id = endpoint.endpoint_id
            row = groups.setdefault(
                instance_id,
                {
                    "api_instance_id": instance_id,
                    "api_instance_name": endpoint.api_instance_name or endpoint.name or instance_id,
                    "api_instance_remark": endpoint.api_instance_remark or "",
                    "provider": endpoint.provider,
                    "base_url": endpoint.base_url,
                    "tenant_id": endpoint.tenant_id,
                    "key_fingerprint": endpoint.key_fingerprint,
                    "enabled_count": 0,
                    "frozen_count": 0,
                    "endpoint_count": 0,
                    "tasks": {},
                    "endpoints": [],
                },
            )
            row["endpoint_count"] += 1
            if endpoint.enabled:
                row["enabled_count"] += 1
            if endpoint.frozen:
                row["frozen_count"] += 1
            row["key_fingerprint"] = row.get("key_fingerprint") or endpoint.key_fingerprint
            task_row = row["tasks"].setdefault(str(endpoint.task or ""), {"task": endpoint.task, "models": [], "count": 0})
            task_row["count"] += 1
            if endpoint.model not in task_row["models"]:
                task_row["models"].append(endpoint.model)
            row["endpoints"].append(endpoint.model_dump(exclude={"api_key"}))
        out = []
        for row in groups.values():
            row["tasks"] = list(row["tasks"].values())
            out.append(row)
        out.sort(key=lambda x: str(x.get("api_instance_name") or x.get("api_instance_id") or ""))
        return out

    def _persist_snapshot_locked(self) -> None:
        self._store.save_snapshot(
            {
                "default_endpoint_id": self._snapshot.default_endpoint_id,
                "default_endpoint_ids": self._snapshot.default_endpoint_ids,
            },
            [item.model_dump() for item in self._snapshot.endpoints],
        )

    def _refresh_from_store_locked(self) -> bool:
        settings, endpoints = self._store.load()
        self._last_refresh_monotonic = time.monotonic()
        if not endpoints and not settings.get("_initialized"):
            return False
        default_endpoint_id = settings.get("default_endpoint_id")
        default_endpoint_ids = settings.get("default_endpoint_ids", {})
        if not endpoints:
            default_endpoint_id = None
            default_endpoint_ids = {}
        next_snapshot = ApiPoolSnapshot(
            default_endpoint_id=default_endpoint_id,
            default_endpoint_ids=default_endpoint_ids,
            endpoints=[ApiEndpoint(**item) for item in endpoints],
        )
        changed = next_snapshot != self._snapshot
        self._snapshot = next_snapshot
        if changed:
            self._rr_counters.clear()
        return True

    def _load_from_file_locked(self, file_path: str) -> ApiPoolSnapshot:
        raise RuntimeError("API pool file loading is disabled; use API management database")
        if not os.path.exists(file_path):
            self._snapshot = ApiPoolSnapshot()
            return self._snapshot

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        endpoints = [ApiEndpoint(**item) for item in data.get("endpoints", [])]
        self._snapshot = ApiPoolSnapshot(
            default_endpoint_id=data.get("default_endpoint_id"),
            default_endpoint_ids=data.get("default_endpoint_ids", {}),
            endpoints=endpoints,
        )
        return self._snapshot


def _fingerprint_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"fp_{digest[:16]}"


def _refresh_interval_seconds() -> float:
    raw = (os.getenv("API_POOL_REFRESH_SECONDS", "5") or "5").strip()
    try:
        return max(0.5, min(float(raw), 300.0))
    except ValueError:
        return 5.0


api_pool_manager = ApiPoolManager()
aux_api_pool_manager = ApiPoolManager(namespace="aux_api_pool")
