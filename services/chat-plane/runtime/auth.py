from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List


class AuthError(Exception):
    pass


KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
BASE64_URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_TOKEN_LENGTH = 8192
MAX_PREVIOUS_KEYS = 3
CLOCK_SKEW_SECONDS = 60


@dataclass
class AuthClaims:
    sub: str
    tenant_id: str
    roles: List[str]
    exp: int
    raw: Dict[str, Any]


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    if not raw or not BASE64_URL_PATTERN.fullmatch(raw):
        raise AuthError("invalid token encoding")
    padding = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode((raw + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthError("invalid token encoding") from exc


def _decode_object(raw: str, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(_b64url_decode(raw).decode("utf-8"))
    except (AuthError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthError(f"invalid token {label}") from exc
    if not isinstance(value, dict):
        raise AuthError(f"invalid token {label}")
    return value


def _parse_previous_keys(raw: str) -> Dict[str, str]:
    previous: Dict[str, str] = {}
    for item in (part.strip() for part in str(raw or "").split(",")):
        if not item:
            continue
        if ":" not in item:
            raise RuntimeError("Chat JWT previous keys must use kid:secret entries")
        key_id, secret = (part.strip() for part in item.split(":", 1))
        if not KEY_ID_PATTERN.fullmatch(key_id) or not secret or key_id in previous:
            raise RuntimeError("Chat JWT previous key entry is invalid or duplicated")
        previous[key_id] = secret
    if len(previous) > MAX_PREVIOUS_KEYS:
        raise RuntimeError(f"at most {MAX_PREVIOUS_KEYS} previous Chat JWT keys are allowed")
    return previous


def _parse_expiry(raw: str, label: str) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp())


def _signature_matches(signing_input: str, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return hmac.compare_digest(_b64url_encode(expected), signature)


def decode_access_token(
    token: str,
    *,
    secret: str,
    key_id: str = "control-dev-primary",
    previous_keys: str = "",
    previous_expires_at: str = "",
    legacy_accept_until: str = "",
    issuer: str = "agentpowflow-control",
    audience: str = "control-plane",
    require_tenant: bool = False,
    now: int | None = None,
) -> AuthClaims:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise AuthError("invalid token format")
    if not KEY_ID_PATTERN.fullmatch(key_id) or not secret or not issuer or not audience:
        raise RuntimeError("Chat JWT verifier primary configuration is invalid")
    previous = _parse_previous_keys(previous_keys)
    if key_id in previous or len({secret, *previous.values()}) != len(previous) + 1:
        raise RuntimeError("Chat JWT key ids and secrets must be distinct")
    previous_expiry = _parse_expiry(previous_expires_at, "Chat JWT previous key expiry")
    legacy_expiry = _parse_expiry(legacy_accept_until, "Chat JWT legacy acceptance deadline")
    if previous and previous_expiry is None:
        raise RuntimeError("Chat JWT previous keys require an expiry")
    if not previous and previous_expiry is not None:
        raise RuntimeError("Chat JWT previous key expiry requires previous keys")

    current_time = int(time.time()) if now is None else int(now)
    if previous_expiry is not None and (previous_expiry <= current_time or previous_expiry - current_time > 604800):
        raise RuntimeError("Chat JWT previous key expiry must be in the future and at most 7 days away")
    if legacy_expiry is not None and (legacy_expiry <= current_time or legacy_expiry - current_time > 604800):
        raise RuntimeError("Chat JWT legacy acceptance deadline must be in the future and at most 7 days away")
    parts = token.split(".")
    payload: Dict[str, Any]
    if len(parts) == 3:
        header_part, payload_part, signature = parts
        header = _decode_object(header_part, "header")
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise AuthError("unsupported token header")
        token_key_id = str(header.get("kid") or "")
        if token_key_id == key_id:
            verification_secret = secret
        elif token_key_id in previous and previous_expiry is not None and current_time < previous_expiry:
            verification_secret = previous[token_key_id]
        else:
            raise AuthError("unknown or expired token key id")
        signing_input = f"{header_part}.{payload_part}"
        if not _signature_matches(signing_input, signature, verification_secret):
            raise AuthError("invalid token signature")
        payload = _decode_object(payload_part, "payload")
        if payload.get("iss") != issuer or payload.get("aud") != audience:
            raise AuthError("token issuer or audience mismatch")
    elif len(parts) == 2:
        if legacy_expiry is None or current_time >= legacy_expiry:
            raise AuthError("legacy token format expired")
        payload_part, signature = parts
        candidates = [secret]
        if previous_expiry is not None and current_time < previous_expiry:
            candidates.extend(previous.values())
        if not any(_signature_matches(payload_part, signature, candidate) for candidate in candidates):
            raise AuthError("invalid token signature")
        payload = _decode_object(payload_part, "payload")
    else:
        raise AuthError("invalid token format")

    try:
        exp = int(payload.get("exp", 0) or 0)
        iat = int(payload.get("iat", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise AuthError("invalid token timestamps") from exc
    if exp <= current_time:
        raise AuthError("token expired")
    if iat <= 0 or iat > current_time + CLOCK_SKEW_SECONDS or exp <= iat:
        raise AuthError("invalid token timestamps")

    token_type = str(payload.get("token_type") or "access").strip().lower()
    if token_type != "access":
        raise AuthError("token type mismatch")

    sub = str(payload.get("sub") or payload.get("actor_id") or "").strip()
    if not sub:
        raise AuthError("token subject missing")

    tenant_id = str(payload.get("tenant_id") or "").strip()
    if require_tenant and not tenant_id:
        raise AuthError("token tenant missing")
    tenant_id = tenant_id or "platform"
    roles_raw = payload.get("roles")
    roles: List[str] = []
    if isinstance(roles_raw, list):
        for item in roles_raw:
            role = str(item or "").strip()
            if role:
                roles.append(role)
    single_role = str(payload.get("role") or "").strip()
    if single_role and single_role not in roles:
        roles.append(single_role)
    return AuthClaims(sub=sub, tenant_id=tenant_id, roles=roles, exp=exp, raw=payload)
