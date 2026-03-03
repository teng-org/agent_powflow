from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

_overrides: Dict[str, Any] = {}


@lru_cache(maxsize=1)
def read_runtime_config() -> Dict[str, Any]:
    p = os.getenv("RUNTIME_CONFIG_PATH", "/app/config/runtime-config.json")
    path = Path(p)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_setting(name: str, default: Any = None) -> Any:
    if name in _overrides:
        return _overrides[name]
    env_name = name.upper()
    if env_name in os.environ:
        return os.environ[env_name]
    return read_runtime_config().get(name, default)


def set_overrides(updates: Dict[str, Any]) -> None:
    for key, value in updates.items():
        _overrides[key] = value


def get_bool(name: str, default: bool) -> bool:
    value = get_setting(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_int(name: str, default: int) -> int:
    value = get_setting(name, default)
    try:
        return int(value)
    except Exception:
        return default
