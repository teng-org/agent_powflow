from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pathlib
import sys
import time
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.jwt_keyring import JwtKeyRing, JwtKeyRingConfigurationError, JwtTokenError


def legacy_token(payload: dict, secret: str) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{encoded}.{signature}"


class JwtKeyRingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = int(time.time())
        self.primary = "primary-value-with-at-least-32-characters"
        self.previous = "previous-value-with-at-least-32-characters"
        expiry = datetime.fromtimestamp(self.now + 3600, tz=timezone.utc).isoformat()
        self.ring = JwtKeyRing(
            primary_id="key-2026-08",
            primary_secret=self.primary,
            previous_keys={"key-2026-07": self.previous},
            previous_expires_at=expiry,
            legacy_accept_until=expiry,
            issuer="issuer-a",
            audience="audience-a",
        )

    def payload(self) -> dict:
        return {"sub": "admin", "role": "PLATFORM_ADMIN", "iat": self.now, "exp": self.now + 600}

    def test_new_tokens_are_standard_hs256_with_primary_kid(self) -> None:
        token = self.ring.sign(self.payload())
        self.assertEqual(3, len(token.split(".")))
        verification = self.ring.verify(token, now=self.now)
        self.assertEqual("key-2026-08", verification.key_id)
        self.assertEqual("primary", verification.key_slot)
        self.assertFalse(verification.legacy_format)
        self.assertEqual("issuer-a", verification.payload["iss"])
        self.assertEqual("audience-a", verification.payload["aud"])

    def test_legacy_primary_and_previous_tokens_are_accepted_during_window(self) -> None:
        primary = self.ring.verify(legacy_token(self.payload(), self.primary), now=self.now)
        previous = self.ring.verify(legacy_token(self.payload(), self.previous), now=self.now)
        self.assertEqual(("primary", True), (primary.key_slot, primary.legacy_format))
        self.assertEqual(("previous:1", True), (previous.key_slot, previous.legacy_format))

    def test_previous_token_is_rejected_after_deadline(self) -> None:
        token = legacy_token(self.payload(), self.previous)
        with self.assertRaisesRegex(JwtTokenError, "legacy token format expired"):
            self.ring.verify(token, now=self.now + 3601)

    def test_legacy_format_is_disabled_without_explicit_deadline(self) -> None:
        ring = JwtKeyRing(primary_id="primary", primary_secret=self.primary, issuer="issuer-a", audience="audience-a")
        with self.assertRaisesRegex(JwtTokenError, "legacy token format expired"):
            ring.verify(legacy_token(self.payload(), self.primary), now=self.now)

    def test_unknown_kid_does_not_fall_back_to_matching_secret(self) -> None:
        token = self.ring.sign(self.payload())
        header_part, payload_part, _ = token.split(".")
        header = {"alg": "HS256", "typ": "JWT", "kid": "unknown"}
        altered_header = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
        signing_input = f"{altered_header}.{payload_part}"
        signature = base64.urlsafe_b64encode(hmac.new(self.primary.encode(), signing_input.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        with self.assertRaisesRegex(JwtTokenError, "key id"):
            self.ring.verify(f"{signing_input}.{signature}", now=self.now)

    def test_issuer_audience_algorithm_and_timestamps_are_enforced(self) -> None:
        other = JwtKeyRing(primary_id="key-2026-08", primary_secret=self.primary, issuer="issuer-b", audience="audience-b")
        with self.assertRaisesRegex(JwtTokenError, "issuer or audience"):
            self.ring.verify(other.sign(self.payload()), now=self.now)
        expired = {**self.payload(), "exp": self.now}
        with self.assertRaisesRegex(JwtTokenError, "expired"):
            self.ring.verify(self.ring.sign(expired), now=self.now)
        future = {**self.payload(), "iat": self.now + 61, "exp": self.now + 600}
        with self.assertRaisesRegex(JwtTokenError, "timestamps"):
            self.ring.verify(self.ring.sign(future), now=self.now)

    def test_previous_keys_require_distinct_ids_secrets_and_expiry(self) -> None:
        with self.assertRaises(JwtKeyRingConfigurationError):
            JwtKeyRing(primary_id="primary", primary_secret=self.primary, previous_keys={"old": self.previous}, issuer="i", audience="a")
        with self.assertRaises(JwtKeyRingConfigurationError):
            JwtKeyRing(primary_id="primary", primary_secret=self.primary, previous_keys={"old": self.primary}, previous_expires_at="2030-01-01T00:00:00Z", issuer="i", audience="a")
        too_far = datetime.fromtimestamp(self.now + 604900, tz=timezone.utc).isoformat()
        with self.assertRaisesRegex(JwtKeyRingConfigurationError, "at most 7 days"):
            JwtKeyRing(primary_id="primary", primary_secret=self.primary, legacy_accept_until=too_far, issuer="i", audience="a")


if __name__ == "__main__":
    unittest.main()
