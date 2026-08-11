from __future__ import annotations

import hashlib
import pathlib
import sys
import unittest

from cryptography.fernet import Fernet

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.workflow_secret_rotation import WorkflowSecretRotationError, prepare_ciphertexts


class WorkflowSecretRotationPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.primary = Fernet.generate_key().decode()
        self.previous = Fernet.generate_key().decode()

    @staticmethod
    def row(secret_id: str, plaintext: str, key: str) -> dict[str, str]:
        return {
            "secret_id": secret_id,
            "ciphertext": Fernet(key.encode()).encrypt(plaintext.encode()).decode(),
            "fingerprint": hashlib.sha256(plaintext.encode()).hexdigest()[:12],
        }

    def test_plan_rotates_previous_and_leaves_primary_unchanged(self) -> None:
        rows = [
            self.row("old", "old-value", self.previous),
            self.row("new", "new-value", self.primary),
        ]
        prepared = prepare_ciphertexts(rows, self.primary, [self.previous])
        by_id = {item.secret_id: item for item in prepared}
        self.assertEqual("previous:1", by_id["old"].source_slot)
        self.assertIsNotNone(by_id["old"].rotated_ciphertext)
        self.assertEqual("primary", by_id["new"].source_slot)
        self.assertIsNone(by_id["new"].rotated_ciphertext)
        self.assertEqual(
            b"old-value",
            Fernet(self.primary.encode()).decrypt(by_id["old"].rotated_ciphertext.encode()),
        )

    def test_unknown_key_and_fingerprint_mismatch_fail_before_updates(self) -> None:
        unknown = Fernet.generate_key().decode()
        with self.assertRaisesRegex(WorkflowSecretRotationError, "cannot be decrypted"):
            prepare_ciphertexts([self.row("unknown", "value", unknown)], self.primary, [self.previous])

        mismatch = self.row("mismatch", "value", self.previous)
        mismatch["fingerprint"] = "0" * 12
        with self.assertRaisesRegex(WorkflowSecretRotationError, "fingerprint mismatch"):
            prepare_ciphertexts([mismatch], self.primary, [self.previous])

    def test_primary_and_previous_keys_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(WorkflowSecretRotationError, "distinct"):
            prepare_ciphertexts([], self.primary, [self.primary])


if __name__ == "__main__":
    unittest.main()
