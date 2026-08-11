from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from psycopg.rows import dict_row


ROTATION_ADVISORY_LOCK_KEY = 4_701_126_941_038_517_924
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
CONFIRMATION = "ROTATE_WORKFLOW_SECRET_MASTER_KEY"


class WorkflowSecretRotationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RotationReport:
    mode: str
    total_rows: int
    already_primary: int
    needs_rotation: int
    applied: int
    verified_primary: int


@dataclass(frozen=True)
class PreparedCiphertext:
    secret_id: str
    original_ciphertext: str
    rotated_ciphertext: str | None
    source_slot: str


def parse_previous_keys(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    values = raw.split(",") if isinstance(raw, str) else raw
    return tuple(str(value).strip() for value in values if str(value).strip())


def validated_fernets(primary_key: str, previous_keys: Sequence[str]) -> tuple[tuple[str, Fernet], ...]:
    keys = (str(primary_key or "").strip(), *(str(value).strip() for value in previous_keys))
    if not keys[0]:
        raise WorkflowSecretRotationError("WORKFLOW_SECRET_MASTER_KEY is required")
    if any(not value for value in keys) or len(set(keys)) != len(keys):
        raise WorkflowSecretRotationError("workflow Secret master keys must be non-empty and distinct")
    result: list[tuple[str, Fernet]] = []
    for index, key in enumerate(keys):
        slot = "primary" if index == 0 else f"previous:{index}"
        try:
            result.append((slot, Fernet(key.encode("ascii"))))
        except (UnicodeEncodeError, ValueError) as exc:
            raise WorkflowSecretRotationError(f"{slot} is not a valid Fernet key") from exc
    return tuple(result)


def _fingerprint(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:12]


def prepare_ciphertexts(
    rows: Iterable[Mapping[str, object]],
    primary_key: str,
    previous_keys: Sequence[str],
) -> tuple[PreparedCiphertext, ...]:
    fernets = validated_fernets(primary_key, previous_keys)
    primary_fernet = fernets[0][1]
    prepared: list[PreparedCiphertext] = []
    for row in rows:
        secret_id = str(row.get("secret_id") or "").strip()
        ciphertext = str(row.get("ciphertext") or "")
        fingerprint = str(row.get("fingerprint") or "")
        if not secret_id or not ciphertext or not fingerprint:
            raise WorkflowSecretRotationError("workflow_secrets contains an incomplete row")
        try:
            encoded = ciphertext.encode("ascii")
        except UnicodeEncodeError as exc:
            raise WorkflowSecretRotationError(f"secret_id={secret_id} ciphertext is not ASCII Fernet data") from exc

        plaintext: str | None = None
        source_slot = ""
        for slot, fernet in fernets:
            try:
                plaintext = fernet.decrypt(encoded).decode("utf-8")
                source_slot = slot
                break
            except (InvalidToken, UnicodeDecodeError):
                continue
        if plaintext is None:
            raise WorkflowSecretRotationError(f"secret_id={secret_id} cannot be decrypted by primary or previous keys")
        if not hmac.compare_digest(_fingerprint(plaintext), fingerprint):
            raise WorkflowSecretRotationError(f"secret_id={secret_id} plaintext fingerprint mismatch")

        rotated = None
        if source_slot != "primary":
            rotated = primary_fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        prepared.append(
            PreparedCiphertext(
                secret_id=secret_id,
                original_ciphertext=ciphertext,
                rotated_ciphertext=rotated,
                source_slot=source_slot,
            )
        )
    return tuple(prepared)


class WorkflowSecretMasterKeyRotator:
    def __init__(
        self,
        *,
        dsn: str,
        primary_key: str,
        previous_keys: Sequence[str],
        schema: str = "public",
        actor: str = "system:master-key-rotation",
        lock_timeout_seconds: int = 10,
    ) -> None:
        if not str(dsn or "").strip():
            raise WorkflowSecretRotationError("POSTGRES_DSN is required")
        if not SCHEMA_PATTERN.fullmatch(schema):
            raise WorkflowSecretRotationError("schema must be a safe PostgreSQL identifier")
        if not actor or len(actor) > 128:
            raise WorkflowSecretRotationError("actor must contain 1 to 128 characters")
        if lock_timeout_seconds < 1 or lock_timeout_seconds > 300:
            raise WorkflowSecretRotationError("lock_timeout_seconds must be between 1 and 300")
        validated_fernets(primary_key, previous_keys)
        self.dsn = dsn
        self.primary_key = primary_key
        self.previous_keys = tuple(previous_keys)
        self.schema = schema
        self.actor = actor
        self.lock_timeout_seconds = lock_timeout_seconds

    def execute(self, *, apply: bool = False) -> RotationReport:
        table = sql.Identifier(self.schema, "workflow_secrets")
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('lock_timeout', %s, true)", (f"{self.lock_timeout_seconds}s",))
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (ROTATION_ADVISORY_LOCK_KEY,))
                lock_mode = sql.SQL("SHARE ROW EXCLUSIVE") if apply else sql.SQL("SHARE")
                cur.execute(sql.SQL("LOCK TABLE {} IN {} MODE").format(table, lock_mode))
                cur.execute(
                    sql.SQL("SELECT secret_id,ciphertext,fingerprint FROM {} ORDER BY secret_id FOR UPDATE").format(table)
                )
                rows = list(cur.fetchall())
                prepared = prepare_ciphertexts(rows, self.primary_key, self.previous_keys)
                needs_rotation = [item for item in prepared if item.rotated_ciphertext is not None]

                if apply:
                    for item in needs_rotation:
                        cur.execute(
                            sql.SQL(
                                "UPDATE {} SET ciphertext=%s,updated_by=%s,updated_at=NOW() "
                                "WHERE secret_id=%s AND ciphertext=%s"
                            ).format(table),
                            (item.rotated_ciphertext, self.actor, item.secret_id, item.original_ciphertext),
                        )
                        if cur.rowcount != 1:
                            raise WorkflowSecretRotationError(
                                f"secret_id={item.secret_id} changed concurrently during rotation"
                            )

                    cur.execute(
                        sql.SQL("SELECT secret_id,ciphertext,fingerprint FROM {} ORDER BY secret_id FOR UPDATE").format(table)
                    )
                    verified = prepare_ciphertexts(list(cur.fetchall()), self.primary_key, ())
                    if any(item.source_slot != "primary" for item in verified):
                        raise WorkflowSecretRotationError("post-rotation primary-key verification failed")
                    verified_primary = len(verified)
                else:
                    verified_primary = len(prepared) - len(needs_rotation)

        return RotationReport(
            mode="apply" if apply else "dry-run",
            total_rows=len(prepared),
            already_primary=len(prepared) - len(needs_rotation),
            needs_rotation=len(needs_rotation),
            applied=len(needs_rotation) if apply else 0,
            verified_primary=verified_primary,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate workflow_secrets Fernet ciphertext without printing key material.")
    parser.add_argument("--apply", action="store_true", help="commit re-encryption; default is dry-run")
    parser.add_argument("--confirm", default="", help=f"required with --apply: {CONFIRMATION}")
    parser.add_argument("--schema", default=os.getenv("WORKFLOW_SECRET_SCHEMA", "public"))
    parser.add_argument("--actor", default=os.getenv("WORKFLOW_SECRET_ROTATION_ACTOR", "system:master-key-rotation"))
    parser.add_argument("--lock-timeout-seconds", type=int, default=10)
    args = parser.parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        raise WorkflowSecretRotationError(f"--apply requires --confirm {CONFIRMATION}")

    rotator = WorkflowSecretMasterKeyRotator(
        dsn=os.getenv("POSTGRES_DSN", ""),
        primary_key=os.getenv("WORKFLOW_SECRET_MASTER_KEY", ""),
        previous_keys=parse_previous_keys(os.getenv("WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS", "")),
        schema=args.schema,
        actor=args.actor,
        lock_timeout_seconds=args.lock_timeout_seconds,
    )
    report = rotator.execute(apply=args.apply)
    print("WORKFLOW_SECRET_MASTER_KEY_ROTATION_OK " + json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
