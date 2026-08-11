from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from runtime.auth import AuthError, decode_access_token


SECRET = "unit-test-secret"
KEY_ID = "control-current"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _legacy_token(payload: dict) -> str:
    payload_part = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(SECRET.encode(), payload_part.encode(), hashlib.sha256).digest()
    return f"{payload_part}.{_b64url(signature)}"


def _jwt_token(payload: dict, *, alg: str | None = "HS256", key_id: str = KEY_ID, secret: str = SECRET) -> str:
    header = {"typ": "JWT", "kid": key_id}
    if alg is not None:
        header["alg"] = alg
    header_part = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _decode(token: str, *, require_tenant: bool = False, **kwargs: object):
    return decode_access_token(
        token,
        secret=SECRET,
        key_id=KEY_ID,
        require_tenant=require_tenant,
        **kwargs,
    )


class DecodeAccessTokenTest(unittest.TestCase):
    def valid_payload(self, **overrides: object) -> dict:
        payload = {
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "roles": ["TENANT_USER"],
            "token_type": "access",
            "iat": int(time.time()) - 1,
            "exp": int(time.time()) + 300,
            "iss": "agentpowflow-control",
            "aud": "control-plane",
        }
        payload.update(overrides)
        return payload

    def test_accepts_control_plane_access_jwt(self) -> None:
        claims = _decode(
            _jwt_token(self.valid_payload()),
            require_tenant=True,
        )
        self.assertEqual("user-1", claims.sub)
        self.assertEqual("tenant-a", claims.tenant_id)
        self.assertEqual(["TENANT_USER"], claims.roles)

    def test_accepts_legacy_execution_admin_token_for_compatibility(self) -> None:
        payload = self.valid_payload(role="PLATFORM_ADMIN")
        payload.pop("roles")
        payload.pop("tenant_id")
        deadline = datetime.fromtimestamp(time.time() + 300, tz=timezone.utc).isoformat()
        claims = _decode(_legacy_token(payload), legacy_accept_until=deadline)
        self.assertEqual("platform", claims.tenant_id)
        self.assertEqual(["PLATFORM_ADMIN"], claims.roles)

    def test_rejects_refresh_token(self) -> None:
        with self.assertRaisesRegex(AuthError, "token type mismatch"):
            _decode(_jwt_token(self.valid_payload(token_type="refresh")))

    def test_rejects_missing_or_expired_expiry(self) -> None:
        missing = self.valid_payload()
        missing.pop("exp")
        with self.assertRaisesRegex(AuthError, "token expired"):
            _decode(_jwt_token(missing))
        with self.assertRaisesRegex(AuthError, "token expired"):
            _decode(_jwt_token(self.valid_payload(exp=int(time.time()) - 1)))

    def test_rejects_missing_tenant_for_user_endpoints(self) -> None:
        payload = self.valid_payload()
        payload.pop("tenant_id")
        with self.assertRaisesRegex(AuthError, "token tenant missing"):
            _decode(_jwt_token(payload), require_tenant=True)

    def test_rejects_jwt_without_explicit_hs256_algorithm(self) -> None:
        with self.assertRaisesRegex(AuthError, "unsupported token header"):
            _decode(_jwt_token(self.valid_payload(), alg=None))

    def test_rejects_wrong_issuer_audience_and_unknown_kid(self) -> None:
        with self.assertRaisesRegex(AuthError, "issuer or audience"):
            _decode(_jwt_token(self.valid_payload(aud="execution-plane")))
        with self.assertRaisesRegex(AuthError, "key id"):
            _decode(_jwt_token(self.valid_payload(), key_id="unknown"))

    def test_legacy_format_is_disabled_without_explicit_deadline(self) -> None:
        with self.assertRaisesRegex(AuthError, "legacy token format expired"):
            _decode(_legacy_token(self.valid_payload()))

    def test_legacy_compatibility_deadline_cannot_exceed_seven_days(self) -> None:
        deadline = datetime.fromtimestamp(time.time() + 604900, tz=timezone.utc).isoformat()
        with self.assertRaisesRegex(RuntimeError, "at most 7 days"):
            _decode(_legacy_token(self.valid_payload()), legacy_accept_until=deadline)


if __name__ == "__main__":
    unittest.main()
