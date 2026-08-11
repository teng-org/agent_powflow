from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping


KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_TOKEN_LENGTH = 8192
MAX_PREVIOUS_KEYS = 3


class JwtTokenError(RuntimeError):
    pass


class JwtKeyRingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class JwtVerification:
    payload: dict
    key_id: str
    key_slot: str
    legacy_format: bool


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    if not raw or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise JwtTokenError("invalid token encoding")
    try:
        return base64.urlsafe_b64decode((raw + ("=" * (-len(raw) % 4))).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise JwtTokenError("invalid token encoding") from exc


def _decode_object(raw: str, label: str) -> dict:
    try:
        value = json.loads(_b64url_decode(raw).decode("utf-8"))
    except (JwtTokenError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JwtTokenError(f"invalid token {label}") from exc
    if not isinstance(value, dict):
        raise JwtTokenError(f"invalid token {label}")
    return value


def parse_previous_keys(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (part.strip() for part in str(raw or "").split(",")):
        if not item:
            continue
        if ":" not in item:
            raise JwtKeyRingConfigurationError("JWT previous keys must use kid:secret entries")
        key_id, secret = (part.strip() for part in item.split(":", 1))
        if not KEY_ID_PATTERN.fullmatch(key_id) or not secret:
            raise JwtKeyRingConfigurationError("JWT previous key entry is invalid")
        if key_id in result:
            raise JwtKeyRingConfigurationError("JWT previous key ids must be unique")
        result[key_id] = secret
    if len(result) > MAX_PREVIOUS_KEYS:
        raise JwtKeyRingConfigurationError(f"at most {MAX_PREVIOUS_KEYS} previous JWT keys are allowed")
    return result


def parse_expiry(value: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JwtKeyRingConfigurationError("JWT previous key expiry must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise JwtKeyRingConfigurationError("JWT previous key expiry must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp())


class JwtKeyRing:
    def __init__(
        self,
        *,
        primary_id: str,
        primary_secret: str,
        previous_keys: Mapping[str, str] | None = None,
        previous_expires_at: str = "",
        legacy_accept_until: str = "",
        issuer: str,
        audience: str,
        clock_skew_seconds: int = 60,
    ) -> None:
        if not KEY_ID_PATTERN.fullmatch(str(primary_id or "")):
            raise JwtKeyRingConfigurationError("JWT primary key id is invalid")
        if not str(primary_secret or ""):
            raise JwtKeyRingConfigurationError("JWT primary secret is required")
        if not issuer or not audience:
            raise JwtKeyRingConfigurationError("JWT issuer and audience are required")
        if clock_skew_seconds < 0 or clock_skew_seconds > 300:
            raise JwtKeyRingConfigurationError("JWT clock skew must be between 0 and 300 seconds")
        previous = dict(previous_keys or {})
        if len(previous) > MAX_PREVIOUS_KEYS:
            raise JwtKeyRingConfigurationError(f"at most {MAX_PREVIOUS_KEYS} previous JWT keys are allowed")
        if primary_id in previous:
            raise JwtKeyRingConfigurationError("JWT primary and previous key ids must be distinct")
        if any(not KEY_ID_PATTERN.fullmatch(key_id) or not secret for key_id, secret in previous.items()):
            raise JwtKeyRingConfigurationError("JWT previous key entry is invalid")
        all_secrets = [primary_secret, *previous.values()]
        if len(set(all_secrets)) != len(all_secrets):
            raise JwtKeyRingConfigurationError("JWT primary and previous secrets must be distinct")
        expiry = parse_expiry(previous_expires_at)
        legacy_expiry = parse_expiry(legacy_accept_until)
        current_time = int(time.time())
        if previous and expiry is None:
            raise JwtKeyRingConfigurationError("JWT previous keys require an expiry")
        if not previous and expiry is not None:
            raise JwtKeyRingConfigurationError("JWT previous key expiry requires previous keys")
        if expiry is not None and (expiry <= current_time or expiry - current_time > 604800):
            raise JwtKeyRingConfigurationError("JWT previous key expiry must be in the future and at most 7 days away")
        if legacy_expiry is not None and (legacy_expiry <= current_time or legacy_expiry - current_time > 604800):
            raise JwtKeyRingConfigurationError("JWT legacy acceptance deadline must be in the future and at most 7 days away")
        self.primary_id = primary_id
        self.primary_secret = primary_secret
        self.previous_keys = previous
        self.previous_expiry = expiry
        self.legacy_expiry = legacy_expiry
        self.issuer = issuer
        self.audience = audience
        self.clock_skew_seconds = clock_skew_seconds

    @classmethod
    def from_config(
        cls,
        *,
        primary_id: str,
        primary_secret: str,
        previous_keys: str,
        previous_expires_at: str,
        legacy_accept_until: str,
        issuer: str,
        audience: str,
    ) -> "JwtKeyRing":
        return cls(
            primary_id=primary_id,
            primary_secret=primary_secret,
            previous_keys=parse_previous_keys(previous_keys),
            previous_expires_at=previous_expires_at,
            legacy_accept_until=legacy_accept_until,
            issuer=issuer,
            audience=audience,
        )

    def sign(self, payload: Mapping[str, object]) -> str:
        claims = dict(payload)
        claims["iss"] = self.issuer
        claims["aud"] = self.audience
        header = {"alg": "HS256", "typ": "JWT", "kid": self.primary_id}
        header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_part = _b64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}"
        return f"{signing_input}.{self._signature(signing_input, self.primary_secret)}"

    def verify(self, token: str, *, now: int | None = None) -> JwtVerification:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise JwtTokenError("invalid token format")
        parts = token.split(".")
        current_time = int(time.time()) if now is None else int(now)
        if len(parts) == 3:
            header_part, payload_part, signature_part = parts
            header = _decode_object(header_part, "header")
            if header.get("alg") != "HS256" or header.get("typ") != "JWT":
                raise JwtTokenError("unsupported token header")
            key_id = str(header.get("kid") or "")
            secret, slot = self._key_for_id(key_id, current_time)
            signing_input = f"{header_part}.{payload_part}"
            payload = _decode_object(payload_part, "payload")
            if payload.get("iss") != self.issuer or payload.get("aud") != self.audience:
                raise JwtTokenError("token issuer or audience mismatch")
            legacy = False
        elif len(parts) == 2:
            if self.legacy_expiry is None or current_time >= self.legacy_expiry:
                raise JwtTokenError("legacy token format expired")
            payload_part, signature_part = parts
            payload = _decode_object(payload_part, "payload")
            signing_input = payload_part
            key_id = ""
            secret = ""
            slot = ""
            legacy = True
        else:
            raise JwtTokenError("invalid token format")

        if legacy:
            candidates = [(self.primary_id, self.primary_secret, "primary")]
            if self._previous_active(current_time):
                candidates.extend((kid, value, f"previous:{index}") for index, (kid, value) in enumerate(self.previous_keys.items(), 1))
            matched = next(
                ((candidate_id, candidate_slot) for candidate_id, candidate_secret, candidate_slot in candidates
                 if hmac.compare_digest(self._signature(signing_input, candidate_secret), signature_part)),
                None,
            )
            if matched is None:
                raise JwtTokenError("invalid token signature")
            key_id, slot = matched
        elif not hmac.compare_digest(self._signature(signing_input, secret), signature_part):
            raise JwtTokenError("invalid token signature")

        self._validate_times(payload, current_time)
        return JwtVerification(payload=payload, key_id=key_id, key_slot=slot, legacy_format=legacy)

    def _key_for_id(self, key_id: str, now: int) -> tuple[str, str]:
        if key_id == self.primary_id:
            return self.primary_secret, "primary"
        if key_id in self.previous_keys and self._previous_active(now):
            return self.previous_keys[key_id], f"previous:{list(self.previous_keys).index(key_id) + 1}"
        raise JwtTokenError("unknown or expired token key id")

    def _previous_active(self, now: int) -> bool:
        return bool(self.previous_keys and self.previous_expiry is not None and now < self.previous_expiry)

    def _validate_times(self, payload: Mapping[str, object], now: int) -> None:
        try:
            expires_at = int(payload.get("exp", 0))
            issued_at = int(payload.get("iat", 0))
        except (TypeError, ValueError) as exc:
            raise JwtTokenError("invalid token timestamps") from exc
        if expires_at <= now:
            raise JwtTokenError("token expired")
        if issued_at <= 0 or issued_at > now + self.clock_skew_seconds or expires_at <= issued_at:
            raise JwtTokenError("invalid token timestamps")

    @staticmethod
    def _signature(signing_input: str, secret: str) -> str:
        return _b64url_encode(hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest())
