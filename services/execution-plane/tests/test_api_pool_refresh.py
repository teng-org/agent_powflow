import unittest
from unittest.mock import patch

from models.api_pool import ApiEndpoint, ApiPoolManager


def endpoint_payload(endpoint_id: str, model: str, *, frozen: bool = False) -> dict:
    return ApiEndpoint(
        endpoint_id=endpoint_id,
        name=model,
        provider="openai-compatible",
        base_url="https://models.example.test/v1",
        model=model,
        task="llm",
        api_key="test-only",
        frozen=frozen,
    ).model_dump()


class FakeStore:
    def __init__(self, endpoints: list[dict]) -> None:
        self.endpoints = endpoints
        self.loads = 0
        self.fail = False

    def load(self):
        self.loads += 1
        if self.fail:
            raise RuntimeError("database unavailable")
        return {"_initialized": True, "default_endpoint_ids": {}}, list(self.endpoints)


class ApiPoolRefreshTest(unittest.TestCase):
    @patch("models.api_pool.time.monotonic")
    def test_read_path_refreshes_after_ttl(self, monotonic) -> None:
        monotonic.return_value = 10.0
        store = FakeStore([endpoint_payload("old", "model-old")])
        manager = ApiPoolManager()
        manager._store = store
        manager._refresh_seconds = 5.0
        manager.bootstrap()

        store.endpoints = [endpoint_payload("new", "model-new")]
        monotonic.return_value = 12.0
        self.assertEqual("old", manager.get_default_for_tenant("tenant-a").endpoint_id)
        self.assertEqual(1, store.loads)

        monotonic.return_value = 16.0
        self.assertEqual("new", manager.get_default_for_tenant("tenant-a").endpoint_id)
        self.assertEqual(2, store.loads)

    @patch("models.api_pool.time.monotonic")
    def test_refresh_failure_preserves_last_valid_snapshot(self, monotonic) -> None:
        monotonic.return_value = 20.0
        store = FakeStore([endpoint_payload("healthy", "model-healthy")])
        manager = ApiPoolManager()
        manager._store = store
        manager._refresh_seconds = 1.0
        manager.bootstrap()

        store.fail = True
        monotonic.return_value = 22.0
        candidate = manager.get_default_for_tenant("tenant-a")

        self.assertIsNotNone(candidate)
        self.assertEqual("healthy", candidate.endpoint_id)
        self.assertEqual(2, store.loads)

    @patch("models.api_pool.time.monotonic")
    def test_refresh_applies_frozen_state_without_restart(self, monotonic) -> None:
        monotonic.return_value = 30.0
        store = FakeStore([endpoint_payload("primary", "model-primary")])
        manager = ApiPoolManager()
        manager._store = store
        manager._refresh_seconds = 1.0
        manager.bootstrap()

        store.endpoints = [endpoint_payload("primary", "model-primary", frozen=True)]
        monotonic.return_value = 32.0

        self.assertEqual([], manager.list_candidates_for_tenant("tenant-a"))


if __name__ == "__main__":
    unittest.main()
