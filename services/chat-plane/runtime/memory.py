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
    profile_refresh_new_messages_threshold: int
    profile_sample_messages: int
    profile_sample_max_chars: int
    recent_topics_limit: int
    recent_events_limit: int
    budget_total_tokens: int
    budget_l0_ratio: float
    budget_l1_ratio: float
    budget_l2_ratio: float
    budget_l3_ratio: float
    budget_l4_ratio: float
    budget_l5_ratio: float
    keyword_top_k: int
    rerank_enabled: bool
    rerank_batch_size: int

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
            "memory_profile_refresh_new_messages_threshold": self.profile_refresh_new_messages_threshold,
            "memory_profile_sample_messages": self.profile_sample_messages,
            "memory_profile_sample_max_chars": self.profile_sample_max_chars,
            "memory_recent_topics_limit": self.recent_topics_limit,
            "memory_recent_events_limit": self.recent_events_limit,
            "memory_budget_total_tokens": self.budget_total_tokens,
            "memory_budget_l0_ratio": self.budget_l0_ratio,
            "memory_budget_l1_ratio": self.budget_l1_ratio,
            "memory_budget_l2_ratio": self.budget_l2_ratio,
            "memory_budget_l3_ratio": self.budget_l3_ratio,
            "memory_budget_l4_ratio": self.budget_l4_ratio,
            "memory_budget_l5_ratio": self.budget_l5_ratio,
            "memory_keyword_top_k": self.keyword_top_k,
            "memory_rerank_enabled": self.rerank_enabled,
            "memory_rerank_batch_size": self.rerank_batch_size,
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
        profile_refresh_new_messages_threshold=max(
            1, get_int("memory_profile_refresh_new_messages_threshold", 12)
        ),
        profile_sample_messages=max(20, get_int("memory_profile_sample_messages", 80)),
        profile_sample_max_chars=max(1200, get_int("memory_profile_sample_max_chars", 12000)),
        recent_topics_limit=max(1, get_int("memory_recent_topics_limit", 6)),
        recent_events_limit=max(1, get_int("memory_recent_events_limit", 6)),
        budget_total_tokens=max(200, get_int("memory_budget_total_tokens", 1500)),
        budget_l0_ratio=max(0.0, float(get_setting("memory_budget_l0_ratio", 0.30))),
        budget_l1_ratio=max(0.0, float(get_setting("memory_budget_l1_ratio", 0.25))),
        budget_l2_ratio=max(0.0, float(get_setting("memory_budget_l2_ratio", 0.20))),
        budget_l3_ratio=max(0.0, float(get_setting("memory_budget_l3_ratio", 0.05))),
        budget_l4_ratio=max(0.0, float(get_setting("memory_budget_l4_ratio", 0.20))),
        budget_l5_ratio=max(0.0, float(get_setting("memory_budget_l5_ratio", 0.05))),
        keyword_top_k=max(1, get_int("memory_keyword_top_k", 4)),
        rerank_enabled=get_bool("memory_rerank_enabled", False),
        rerank_batch_size=max(1, get_int("memory_rerank_batch_size", 20)),
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
        "memory_profile_refresh_new_messages_threshold": 12,
        "memory_profile_sample_messages": 80,
        "memory_profile_sample_max_chars": 12000,
        "memory_recent_topics_limit": 6,
        "memory_recent_events_limit": 6,
        "memory_budget_total_tokens": 1500,
        "memory_budget_l0_ratio": 0.30,
        "memory_budget_l1_ratio": 0.25,
        "memory_budget_l2_ratio": 0.20,
        "memory_budget_l3_ratio": 0.05,
        "memory_budget_l4_ratio": 0.20,
        "memory_budget_l5_ratio": 0.05,
        "memory_keyword_top_k": 4,
        "memory_rerank_enabled": False,
        "memory_rerank_batch_size": 20,
    }


def normalize_memory_config_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    keys = set(memory_config_defaults().keys())
    clean: Dict[str, Any] = {}
    for k, v in updates.items():
        if k not in keys:
            continue
        if k.endswith("_enabled"):
            clean[k] = bool(v)
        elif k.endswith("_ratio"):
            try:
                clean[k] = float(v)
            except Exception:
                continue
        elif (
            k.endswith("_limit")
            or k.endswith("_budget")
            or k.endswith("_messages")
            or k.endswith("_chars")
            or k.endswith("_max")
            or k.endswith("_seconds")
            or k.endswith("_threshold")
        ):
            try:
                clean[k] = int(v)
            except Exception:
                continue
        else:
            clean[k] = v
    return clean
