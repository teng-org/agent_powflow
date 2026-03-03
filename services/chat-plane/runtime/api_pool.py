from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ChatApiEndpoint:
    endpoint_id: str
    name: str
    provider: str
    base_url: str
    model: str
    task: str
    api_key: str
    tenant_id: str = "*"
    enabled: bool = True
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


@dataclass
class ChatApiPoolSnapshot:
    default_endpoint_id: str
    default_endpoint_ids: Dict[str, str]
    endpoints: List[ChatApiEndpoint]


class ChatApiPoolManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._endpoints: Dict[str, ChatApiEndpoint] = {}
        self._default_endpoint_id: str = ""
        self._default_endpoint_ids: Dict[str, str] = {}

    def reset_from_file(self, path: str) -> ChatApiPoolSnapshot:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        endpoints = [ChatApiEndpoint(**item) for item in (data.get("endpoints") or [])]
        with self._lock:
            self._endpoints = {e.endpoint_id: e for e in endpoints}
            self._default_endpoint_id = str(data.get("default_endpoint_id") or "")
            self._default_endpoint_ids = dict(data.get("default_endpoint_ids") or {})
            if not self._default_endpoint_ids and self._default_endpoint_id:
                self._default_endpoint_ids = {"chat_llm": self._default_endpoint_id}
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
        return items

    def get_default(self, task: str = "chat_llm") -> Optional[ChatApiEndpoint]:
        with self._lock:
            endpoint_id = self._default_endpoint_ids.get(task) or self._default_endpoint_id
            ep = self._endpoints.get(endpoint_id)
            if ep and ep.enabled:
                return ep
            for item in self._endpoints.values():
                if item.task == task and item.enabled:
                    return item
        return None

    def upsert_endpoint(self, endpoint: ChatApiEndpoint) -> ChatApiEndpoint:
        with self._lock:
            self._endpoints[endpoint.endpoint_id] = endpoint
        return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self._lock:
            existed = endpoint_id in self._endpoints
            if existed:
                del self._endpoints[endpoint_id]
                if self._default_endpoint_id == endpoint_id:
                    self._default_endpoint_id = ""
                for task, eid in list(self._default_endpoint_ids.items()):
                    if eid == endpoint_id:
                        del self._default_endpoint_ids[task]
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
        return endpoint_id


def _default_config_path() -> str:
    return os.getenv(
        "CHAT_API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../config/chat_api_pool.default.json"),
    )


chat_api_pool_manager = ChatApiPoolManager()
try:
    chat_api_pool_manager.reset_from_file(os.path.abspath(_default_config_path()))
except Exception:
    pass
