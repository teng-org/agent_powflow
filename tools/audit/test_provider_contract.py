from __future__ import annotations

import importlib.util
import pathlib
import socket
import sys
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "provider_contract.py"
SPEC = importlib.util.spec_from_file_location("provider_contract", MODULE_PATH)
provider_contract = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = provider_contract
SPEC.loader.exec_module(provider_contract)


class ProviderContractTests(unittest.TestCase):
    def config(self, **overrides):
        values = {
            "provider": "test-provider",
            "base_url": "https://api.example.com/v1",
            "chat_model": "chat-test",
            "embedding_model": "embedding-test",
            "api_key_env": "TEST_PROVIDER_KEY",
            "input_per_million_usd": 1.0,
            "output_per_million_usd": 2.0,
            "embedding_per_million_usd": 0.1,
            "max_cost_usd": 0.01,
            "allow_paid_provider_calls": True,
        }
        values.update(overrides)
        return provider_contract.ContractConfig(**values)

    def test_paid_call_approval_is_mandatory(self):
        config = self.config(allow_paid_provider_calls=False)
        with mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not run")) as resolver:
            with self.assertRaisesRegex(provider_contract.ContractError, "disabled"):
                provider_contract.validate_config(config, {"TEST_PROVIDER_KEY": "secret"})
        resolver.assert_not_called()

    def test_preflight_cost_ceiling_rejects_before_request(self):
        config = self.config(max_cost_usd=0.000001)
        with mock.patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with self.assertRaisesRegex(provider_contract.ContractError, "exceeds ceiling"):
                provider_contract.validate_config(config, {"TEST_PROVIDER_KEY": "secret"})

    def test_empty_model_is_rejected_before_dns(self):
        config = self.config(chat_model="")
        with mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not run")) as resolver:
            with self.assertRaisesRegex(provider_contract.ContractError, "must not be empty"):
                provider_contract.validate_config(config, {"TEST_PROVIDER_KEY": "secret"})
        resolver.assert_not_called()

    def test_success_report_is_sanitized_and_records_contract_evidence(self):
        calls = []

        def requester(url, payload, headers, timeout):
            calls.append((url, payload, headers, timeout))
            if url.endswith("/chat/completions"):
                return 200, {
                    "choices": [{"message": {"content": "APF_OK"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
                }, {"x-request-id": "chat-request", "x-ratelimit-remaining-requests": "99"}
            return 200, {
                "data": [{"embedding": [0.1, -0.2, 0.3]}],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }, {"request-id": "embedding-request"}

        with mock.patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            report = provider_contract.run_contract(
                self.config(require_rate_limit_headers=True),
                environ={"TEST_PROVIDER_KEY": "secret-value"},
                requester=requester,
            )
        self.assertTrue(report["passed"])
        self.assertEqual(3, report["embedding"]["dimensions"])
        self.assertEqual(["x-ratelimit-remaining-requests"], report["rate_limit_headers_observed"])
        self.assertEqual("x-request-id", report["chat"]["request_id_header"])
        self.assertNotIn("APF_OK", str(report))
        self.assertNotIn("secret-value", str(report))
        self.assertEqual(2, len(calls))
        self.assertNotEqual(calls[0][2]["Idempotency-Key"], calls[1][2]["Idempotency-Key"])

    def test_missing_usage_is_a_contract_failure(self):
        def requester(url, payload, headers, timeout):
            return 200, {"choices": [{"message": {"content": "APF_OK"}}]}, {}

        with mock.patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with self.assertRaisesRegex(provider_contract.ContractError, "missing usage"):
                provider_contract.run_contract(
                    self.config(),
                    environ={"TEST_PROVIDER_KEY": "secret-value"},
                    requester=requester,
                )


if __name__ == "__main__":
    unittest.main()
