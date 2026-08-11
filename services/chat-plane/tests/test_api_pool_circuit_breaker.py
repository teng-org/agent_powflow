from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch


CHAT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from runtime.api_pool import ChatApiEndpoint, ChatApiPoolManager


class ChatApiPoolCircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = ChatApiPoolManager()
        self.manager._save_state_locked = lambda: None
        self.endpoint = ChatApiEndpoint(
            endpoint_id="provider-a",
            name="Provider A",
            provider="openai_compatible",
            base_url="https://provider.example/v1",
            model="chat-model",
            task="chat_llm",
            api_key="test-only-key",
            circuit_fail_threshold=2,
            circuit_open_seconds=30,
        )
        self.manager.upsert_endpoint(self.endpoint)

    def test_open_circuit_is_not_selected_or_acquired(self) -> None:
        with patch("runtime.api_pool.time.time", return_value=1000.0):
            self.manager.record_result("provider-a", ok=False, latency_ms=10)
            self.manager.record_result("provider-a", ok=False, latency_ms=10)
            self.assertEqual([], self.manager.list_candidates("chat_llm"))
            self.assertIsNone(self.manager.choose_endpoint("chat_llm"))
            self.assertFalse(self.manager.try_acquire_slot("provider-a"))
            self.assertEqual("OPEN", self.manager.endpoint_health()[0]["circuit_state"])

    def test_half_open_allows_one_probe_and_success_closes(self) -> None:
        self.manager._health["provider-a"].update(
            {"consecutive_failures": 2.0, "circuit_open_until_ts": 999.0}
        )
        with patch("runtime.api_pool.time.time", return_value=1000.0):
            self.assertEqual("HALF_OPEN", self.manager.endpoint_health()[0]["circuit_state"])
            self.assertEqual("provider-a", self.manager.choose_endpoint("chat_llm").endpoint_id)
            self.assertTrue(self.manager.try_acquire_slot("provider-a"))
            self.assertFalse(self.manager.try_acquire_slot("provider-a"))
            self.manager.record_result("provider-a", ok=True, latency_ms=8)
            self.manager.release_slot("provider-a")
            self.assertEqual("CLOSED", self.manager.endpoint_health()[0]["circuit_state"])
            self.assertEqual(0, self.manager.endpoint_health()[0]["consecutive_failures"])

    def test_failed_half_open_probe_reopens_full_window(self) -> None:
        self.manager._health["provider-a"].update(
            {"consecutive_failures": 2.0, "circuit_open_until_ts": 999.0}
        )
        with patch("runtime.api_pool.time.time", return_value=1000.0):
            self.assertTrue(self.manager.try_acquire_slot("provider-a"))
            self.manager.record_result("provider-a", ok=False, latency_ms=9)
            self.manager.release_slot("provider-a")
            health = self.manager.endpoint_health()[0]
            self.assertEqual("OPEN", health["circuit_state"])
            self.assertFalse(self.manager.try_acquire_slot("provider-a"))
            self.assertEqual(1030.0, self.manager._health["provider-a"]["circuit_open_until_ts"])

    def test_cancelled_half_open_probe_releases_claim(self) -> None:
        self.manager._health["provider-a"].update(
            {"consecutive_failures": 2.0, "circuit_open_until_ts": 999.0}
        )
        with patch("runtime.api_pool.time.time", return_value=1000.0):
            self.assertTrue(self.manager.try_acquire_slot("provider-a"))
            self.manager.release_slot("provider-a")
            self.assertTrue(self.manager.try_acquire_slot("provider-a"))

    def test_restored_half_open_probe_claim_is_cleared(self) -> None:
        loaded = self.manager._load_state_payload_locked(
            {
                "health": {
                    "provider-a": {
                        "consecutive_failures": 2.0,
                        "circuit_open_until_ts": 999.0,
                        "half_open_inflight": True,
                    }
                }
            }
        )

        self.assertTrue(loaded)
        self.assertFalse(self.manager._health["provider-a"]["half_open_inflight"])
        with patch("runtime.api_pool.time.time", return_value=1000.0):
            self.assertTrue(self.manager.try_acquire_slot("provider-a"))

    def test_latency_aware_routing_avoids_persistently_slow_peer(self) -> None:
        peer = ChatApiEndpoint(
            endpoint_id="provider-b",
            name="Provider B",
            provider="openai_compatible",
            base_url="https://provider-b.example/v1",
            model="chat-model",
            task="chat_llm",
            api_key="test-only-key-b",
        )
        self.manager.upsert_endpoint(peer)
        self.manager.record_result("provider-a", ok=True, latency_ms=500)
        self.manager.record_result("provider-b", ok=True, latency_ms=5000)

        selected = {self.manager.choose_endpoint("chat_llm").endpoint_id for _ in range(4)}

        self.assertEqual({"provider-a"}, selected)


if __name__ == "__main__":
    unittest.main()
