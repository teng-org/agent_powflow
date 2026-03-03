from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List


class AuthError(Exception):
    pass


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
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _verify_legacy_execution_token(token: str, secret: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 2:
        raise AuthError("invalid token format")
    payload_part, sig_part = parts
    expected_sig = hmac.new(secret.encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_sig), sig_part):
        raise AuthError("invalid token signature")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise AuthError("invalid token payload") from exc
    return payload if isinstance(payload, dict) else {}


def _verify_jwt_hs256(token: str, secret: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("invalid jwt format")
    header_part, payload_part, sig_part = parts
    try:
        header = json.loads(_b64url_decode(header_part).decode("utf-8"))
    except Exception as exc:
        raise AuthError("invalid jwt header") from exc
    alg = str((header or {}).get("alg") or "").upper()
    if alg and alg != "HS256":
        raise AuthError("unsupported jwt alg")
    signing_input = f"{header_part}.{payload_part}"
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_sig), sig_part):
        raise AuthError("invalid jwt signature")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise AuthError("invalid jwt payload") from exc
    return payload if isinstance(payload, dict) else {}


def decode_access_token(token: str, *, secret: str) -> AuthClaims:
    payload: Dict[str, Any]
    if token.count(".") == 1:
        payload = _verify_legacy_execution_token(token, secret)
    elif token.count(".") == 2:
        payload = _verify_jwt_hs256(token, secret)
    else:
        raise AuthError("invalid token format")

    exp = int(payload.get("exp", 0) or 0)
    if exp and exp < int(time.time()):
        raise AuthError("token expired")

    sub = str(payload.get("sub") or payload.get("actor_id") or "").strip()
    if not sub:
        raise AuthError("token subject missing")

    tenant_id = str(payload.get("tenant_id") or "platform").strip() or "platform"
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

