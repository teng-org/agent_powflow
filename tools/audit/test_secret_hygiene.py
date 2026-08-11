from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit.verify_secret_hygiene import scan_paths, scan_text


class SecretHygieneTest(unittest.TestCase):
    def test_known_token_format_is_detected_without_echoing_value(self) -> None:
        token = "ghp" + "_" + ("A1" * 20)
        findings = scan_text("fixture.env", f"GITHUB_TOKEN={token}\n")
        self.assertTrue(any(item.rule == "GITHUB_TOKEN" for item in findings))
        self.assertTrue(all(token not in item.message for item in findings))

    def test_private_key_marker_is_detected(self) -> None:
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        findings = scan_text("key.pem", marker)
        self.assertEqual(["PRIVATE_KEY_BLOCK"], [item.rule for item in findings])

    def test_high_entropy_sensitive_assignment_is_detected(self) -> None:
        value = "A7mN4xQ2vL9sK6dR8wT5yH3pF0zC1jB"
        findings = scan_text("settings.yml", f'api_key: "{value}"')
        self.assertTrue(any(item.rule == "HIGH_ENTROPY_SECRET_ASSIGNMENT" for item in findings))

    def test_env_reference_and_development_placeholders_are_allowed(self) -> None:
        text = "\n".join(
            [
                "TOKEN=${TOKEN:-dev-token}",
                "password: admin123",
                "api_key: placeholder",
                "secret = os.getenv('SECRET')",
            ]
        )
        self.assertEqual([], scan_text("compose.yml", text))

    def test_runtime_env_filename_is_rejected_even_with_weak_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env.production"
            env_file.write_text("PASSWORD=admin123\n", encoding="utf-8")
            findings = scan_paths(root, [env_file])
        self.assertTrue(any(item.rule == "TRACKED_ENV_FILE" for item in findings))


if __name__ == "__main__":
    unittest.main()
