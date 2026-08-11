from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

_overrides: Dict[str, Any] = {}
_config_cache: Dict[str, Any] = {"data": {}, "signature": None, "loaded_at": 0.0}
_config_lock = threading.Lock()


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _runtime_config_paths() -> tuple[Path, Path | None, Path | None]:
    legacy = Path(os.getenv("RUNTIME_CONFIG_PATH", "/app/config/runtime-config.json"))
    default_raw = os.getenv("RUNTIME_CONFIG_DEFAULT_PATH", "")
    user_raw = os.getenv("RUNTIME_CONFIG_USER_PATH", "")
    default_path = Path(default_raw) if default_raw else legacy.with_name("runtime-config.default.json")
    user_path = Path(user_raw) if user_raw else legacy.with_name("runtime-config.user.json")
    return legacy, default_path, user_path


def _path_mtime(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime if path.exists() else None
    except Exception:
        return None


def read_runtime_config() -> Dict[str, Any]:
    legacy_path, default_path, user_path = _runtime_config_paths()
    now = time.time()
    # 最多每秒检查一次文件变更，减少频繁 IO
    with _config_lock:
        if now - float(_config_cache.get("loaded_at") or 0.0) < 1.0:
            return dict(_config_cache.get("data") or {})

        signature = (
            str(legacy_path),
            _path_mtime(legacy_path),
            str(default_path) if default_path else "",
            _path_mtime(default_path),
            str(user_path) if user_path else "",
            _path_mtime(user_path),
        )
        if _config_cache.get("signature") == signature:
            _config_cache["loaded_at"] = now
            return dict(_config_cache.get("data") or {})

        default_data = _read_json_dict(default_path) if default_path else {}
        if not default_data:
            default_data = _read_json_dict(legacy_path)
        user_data = _read_json_dict(user_path) if user_path else {}
        if not user_data and user_path and user_path.resolve() != legacy_path.resolve():
            user_data = _read_json_dict(legacy_path)

        data = dict(default_data)
        data.update(user_data)
        _config_cache["data"] = data
        _config_cache["signature"] = signature
        _config_cache["loaded_at"] = now
        return dict(data)


def get_setting(name: str, default: Any = None) -> Any:
    if name in _overrides:
        return _overrides[name]
    env_name = name.upper()
    if env_name in os.environ:
        return os.environ[env_name]
    value = read_runtime_config().get(name, default)
    return default if value is None else value


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
