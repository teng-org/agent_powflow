from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .config import get_bool, get_int, get_setting


@dataclass
class MemoryConfig:
    auto_summary_enabled: bool
    hierarchical_enabled: bool
    user_profile_enabled: bool
    eviction_enabled: bool
    recent_messages_limit: int
    context_char_budget: int
    compress_trigger_messages: int
    short_summary_max_chars: int
    long_summary_max_chars: int
    profile_max_chars: int
    memory_item_max: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "memory_auto_summary_enabled": self.auto_summary_enabled,
            "memory_hierarchical_enabled": self.hierarchical_enabled,
            "memory_user_profile_enabled": self.user_profile_enabled,
            "memory_eviction_enabled": self.eviction_enabled,
            "memory_recent_messages_limit": self.recent_messages_limit,
            "memory_context_char_budget": self.context_char_budget,
            "memory_compress_trigger_messages": self.compress_trigger_messages,
            "memory_short_summary_max_chars": self.short_summary_max_chars,
            "memory_long_summary_max_chars": self.long_summary_max_chars,
            "memory_profile_max_chars": self.profile_max_chars,
            "memory_item_max": self.memory_item_max,
        }


def get_memory_config() -> MemoryConfig:
    return MemoryConfig(
        auto_summary_enabled=get_bool("memory_auto_summary_enabled", True),
        hierarchical_enabled=get_bool("memory_hierarchical_enabled", True),
        user_profile_enabled=get_bool("memory_user_profile_enabled", True),
        eviction_enabled=get_bool("memory_eviction_enabled", True),
        recent_messages_limit=max(1, get_int("memory_recent_messages_limit", 10)),
        context_char_budget=max(1000, get_int("memory_context_char_budget", 12000)),
        compress_trigger_messages=max(8, get_int("memory_compress_trigger_messages", 24)),
        short_summary_max_chars=max(120, get_int("memory_short_summary_max_chars", 600)),
        long_summary_max_chars=max(240, get_int("memory_long_summary_max_chars", 1200)),
        profile_max_chars=max(120, get_int("memory_profile_max_chars", 600)),
        memory_item_max=max(20, get_int("memory_item_max", 200)),
    )


def memory_config_defaults() -> Dict[str, Any]:
    return {
        "memory_auto_summary_enabled": True,
        "memory_hierarchical_enabled": True,
        "memory_user_profile_enabled": True,
        "memory_eviction_enabled": True,
        "memory_recent_messages_limit": 10,
        "memory_context_char_budget": 12000,
        "memory_compress_trigger_messages": 24,
        "memory_short_summary_max_chars": 600,
        "memory_long_summary_max_chars": 1200,
        "memory_profile_max_chars": 600,
        "memory_item_max": 200,
    }


def normalize_memory_config_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    keys = set(memory_config_defaults().keys())
    clean: Dict[str, Any] = {}
    for k, v in updates.items():
        if k not in keys:
            continue
        if k.endswith("_enabled"):
            clean[k] = bool(v)
        elif k.endswith("_limit") or k.endswith("_budget") or k.endswith("_messages") or k.endswith("_chars") or k.endswith("_max"):
            try:
                clean[k] = int(v)
            except Exception:
                continue
        else:
            clean[k] = v
    return clean

