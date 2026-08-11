import pathlib
import sys
import unittest

from cryptography.fernet import Fernet, InvalidToken

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.workflow_secrets import SecretVaultUnavailable, WorkflowSecretStore


class WorkflowSecretStoreTest(unittest.TestCase):
    def test_vault_fails_closed_without_a_master_key(self):
        store = WorkflowSecretStore(master_key="")
        self.assertFalse(store.available)
        with self.assertRaisesRegex(SecretVaultUnavailable, "not configured"):
            store._fernet()

    def test_valid_master_key_encrypts_without_plaintext_and_wrong_key_cannot_decrypt(self):
        key = Fernet.generate_key().decode()
        store = WorkflowSecretStore(master_key=key)
        plaintext = b"workflow-super-secret"
        ciphertext = store._fernet().encrypt(plaintext)
        self.assertNotIn(plaintext, ciphertext)
        self.assertEqual(plaintext, store._fernet().decrypt(ciphertext))
        with self.assertRaises(InvalidToken):
            Fernet(Fernet.generate_key()).decrypt(ciphertext)

    def test_previous_master_key_supports_read_only_transition(self):
        previous = Fernet.generate_key().decode()
        primary = Fernet.generate_key().decode()
        store = WorkflowSecretStore(master_key=primary, previous_master_keys=[previous])
        old_ciphertext = Fernet(previous.encode()).encrypt(b"old-value").decode()
        new_ciphertext = store._fernet().encrypt(b"new-value").decode()

        self.assertEqual(("old-value", "previous:1"), store._decrypt(old_ciphertext))
        self.assertEqual(("new-value", "primary"), store._decrypt(new_ciphertext))

    def test_invalid_or_reused_previous_key_fails_closed(self):
        primary = Fernet.generate_key().decode()
        self.assertFalse(WorkflowSecretStore(master_key=primary, previous_master_keys=["invalid"]).available)
        self.assertFalse(WorkflowSecretStore(master_key=primary, previous_master_keys=[primary]).available)

    def test_name_and_value_validation_rejects_unsafe_inputs(self):
        for name in ["x", "1secret", "has space", "../secret"]:
            with self.assertRaises(ValueError):
                WorkflowSecretStore._name(name)
        for value in ["", "bad\x00value", "x" * 8193]:
            with self.assertRaises(ValueError):
                WorkflowSecretStore._value(value)


if __name__ == "__main__":
    unittest.main()
