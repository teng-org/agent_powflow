import json
import os
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
    tenant_id: str = "*"
    enabled: bool = True


class ApiPoolSnapshot(BaseModel):
    default_endpoint_id: str | None = None
    default_endpoint_ids: dict[str, str] = Field(default_factory=dict)
    endpoints: list[ApiEndpoint] = Field(default_factory=list)


class ApiPoolManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = ApiPoolSnapshot()
        self._store = ApiPoolMongoStore()

    def bootstrap(self, file_path: str) -> ApiPoolSnapshot:
        """Load from Mongo first; if empty, seed with file defaults."""
        with self._lock:
            try:
                settings, endpoints = self._store.load()
                if endpoints:
                    self._snapshot = ApiPoolSnapshot(
                        default_endpoint_id=settings.get("default_endpoint_id"),
                        default_endpoint_ids=settings.get("default_endpoint_ids", {}),
                        endpoints=[ApiEndpoint(**item) for item in endpoints],
                    )
                    return self._snapshot
            except Exception:
                # DB unavailable at startup: continue with file defaults in memory.
                pass

            seeded = self._load_from_file_locked(file_path)
            try:
                self._persist_snapshot_locked()
            except Exception:
                pass
            return seeded

    def load_from_file(self, file_path: str) -> ApiPoolSnapshot:
        with self._lock:
            return self._load_from_file_locked(file_path)

    def reset_from_file(self, file_path: str) -> ApiPoolSnapshot:
        with self._lock:
            snapshot = self._load_from_file_locked(file_path)
            try:
                self._persist_snapshot_locked()
            except Exception:
                pass
            return snapshot

    def list_endpoints(self, tenant_id: str | None = None, task: str | None = None) -> list[ApiEndpoint]:
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
        with self._lock:
            replaced = False
            for idx, item in enumerate(self._snapshot.endpoints):
                if item.endpoint_id == endpoint.endpoint_id:
                    self._snapshot.endpoints[idx] = endpoint
                    replaced = True
                    break
            if not replaced:
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
            return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
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
            return deleted

    def set_default(self, endpoint_id: str, task: str | None = None) -> str:
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
            return endpoint_id

    def get_default_for_tenant(self, tenant_id: str, task: str = "llm") -> ApiEndpoint | None:
        with self._lock:
            default_id = self._snapshot.default_endpoint_ids.get(task) or self._snapshot.default_endpoint_id
            if default_id:
                for e in self._snapshot.endpoints:
                    if (
                        e.endpoint_id == default_id
                        and e.task == task
                        and e.enabled
                        and e.tenant_id in ("*", tenant_id)
                    ):
                        return e

            for e in self._snapshot.endpoints:
                if e.task == task and e.enabled and e.tenant_id in ("*", tenant_id):
                    return e
            return None

    def _persist_snapshot_locked(self) -> None:
        self._store.save_snapshot(
            {
                "default_endpoint_id": self._snapshot.default_endpoint_id,
                "default_endpoint_ids": self._snapshot.default_endpoint_ids,
            },
            [item.model_dump() for item in self._snapshot.endpoints],
        )

    def _load_from_file_locked(self, file_path: str) -> ApiPoolSnapshot:
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


api_pool_manager = ApiPoolManager()
