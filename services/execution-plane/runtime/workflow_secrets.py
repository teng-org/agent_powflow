from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken


class SecretVaultUnavailable(RuntimeError):
    pass


class WorkflowSecretNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedSecret:
    secret_id: str
    name: str
    value: str
    fingerprint: str
    version: int
    key_slot: str = "primary"


class WorkflowSecretStore:
    NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,127}$")

    def __init__(
        self,
        dsn: str | None = None,
        master_key: str | None = None,
        previous_master_keys: str | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.dsn = dsn or os.getenv("POSTGRES_DSN", "postgresql://apf:apf@localhost:5432/agentpowflow")
        self.master_key = master_key if master_key is not None else os.getenv("WORKFLOW_SECRET_MASTER_KEY", "")
        raw_previous = previous_master_keys
        if raw_previous is None:
            raw_previous = os.getenv("WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS", "")
        if isinstance(raw_previous, str):
            self.previous_master_keys = tuple(item.strip() for item in raw_previous.split(",") if item.strip())
        else:
            self.previous_master_keys = tuple(str(item).strip() for item in raw_previous if str(item).strip())

    @property
    def available(self) -> bool:
        try:
            self._fernets()
            return True
        except SecretVaultUnavailable:
            return False

    def list_metadata(self, tenant_id: str) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT secret_id,name,description,fingerprint,version,status,created_by,updated_by,created_at,updated_at
                FROM workflow_secrets WHERE tenant_id=%s ORDER BY name ASC""",
                (self._required(tenant_id, "tenant_id"),),
            )
            return [dict(row) for row in cur.fetchall()]

    def create(self, *, tenant_id: str, name: str, description: str, value: str, actor_id: str) -> dict:
        safe_name = self._name(name)
        safe_value = self._value(value)
        now = datetime.now(timezone.utc)
        item = {
            "secret_id": str(uuid.uuid4()),
            "name": safe_name,
            "description": str(description or "").strip()[:500],
            "fingerprint": self._fingerprint(safe_value),
            "version": 1,
            "status": "ACTIVE",
            "created_by": self._required(actor_id, "actor_id")[:128],
            "updated_by": self._required(actor_id, "actor_id")[:128],
            "created_at": now,
            "updated_at": now,
        }
        ciphertext = self._fernet().encrypt(safe_value.encode("utf-8")).decode("ascii")
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO workflow_secrets
                    (secret_id,tenant_id,name,description,ciphertext,fingerprint,version,status,created_by,updated_by,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,1,'ACTIVE',%s,%s,%s,%s)""",
                    (
                        item["secret_id"], self._required(tenant_id, "tenant_id"), safe_name, item["description"],
                        ciphertext, item["fingerprint"], item["created_by"], item["updated_by"], now, now,
                    ),
                )
        except Exception as exc:
            if getattr(exc, "sqlstate", "") == "23505":
                raise ValueError("a secret with this name already exists") from exc
            raise
        return item

    def rotate(self, *, tenant_id: str, secret_id: str, value: str, description: str | None, actor_id: str) -> dict:
        safe_value = self._value(value)
        ciphertext = self._fernet().encrypt(safe_value.encode("utf-8")).decode("ascii")
        with self._connect() as conn, conn.cursor() as cur:
            if description is None:
                cur.execute(
                    """UPDATE workflow_secrets SET ciphertext=%s,fingerprint=%s,version=version+1,status='ACTIVE',
                    updated_by=%s,updated_at=NOW() WHERE tenant_id=%s AND secret_id=%s
                    RETURNING secret_id,name,description,fingerprint,version,status,created_by,updated_by,created_at,updated_at""",
                    (
                        ciphertext, self._fingerprint(safe_value), self._required(actor_id, "actor_id")[:128],
                        self._required(tenant_id, "tenant_id"), self._required(secret_id, "secret_id"),
                    ),
                )
            else:
                cur.execute(
                    """UPDATE workflow_secrets SET ciphertext=%s,fingerprint=%s,version=version+1,status='ACTIVE',
                    description=%s,updated_by=%s,updated_at=NOW() WHERE tenant_id=%s AND secret_id=%s
                    RETURNING secret_id,name,description,fingerprint,version,status,created_by,updated_by,created_at,updated_at""",
                    (
                        ciphertext, self._fingerprint(safe_value), str(description).strip()[:500],
                        self._required(actor_id, "actor_id")[:128], self._required(tenant_id, "tenant_id"),
                        self._required(secret_id, "secret_id"),
                    ),
                )
            row = cur.fetchone()
        if not row:
            raise WorkflowSecretNotFound("workflow secret not found")
        return dict(row)

    def disable(self, *, tenant_id: str, secret_id: str, actor_id: str) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE workflow_secrets SET status='DISABLED',updated_by=%s,updated_at=NOW()
                WHERE tenant_id=%s AND secret_id=%s
                RETURNING secret_id,name,description,fingerprint,version,status,created_by,updated_by,created_at,updated_at""",
                (self._required(actor_id, "actor_id")[:128], self._required(tenant_id, "tenant_id"), self._required(secret_id, "secret_id")),
            )
            row = cur.fetchone()
        if not row:
            raise WorkflowSecretNotFound("workflow secret not found")
        return dict(row)

    def delete(self, *, tenant_id: str, secret_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM workflow_secrets WHERE tenant_id=%s AND secret_id=%s AND status='DISABLED'",
                (self._required(tenant_id, "tenant_id"), self._required(secret_id, "secret_id")),
            )
            if cur.rowcount == 1:
                return True
            cur.execute(
                "SELECT status FROM workflow_secrets WHERE tenant_id=%s AND secret_id=%s",
                (self._required(tenant_id, "tenant_id"), self._required(secret_id, "secret_id")),
            )
            row = cur.fetchone()
        if not row:
            raise WorkflowSecretNotFound("workflow secret not found")
        raise ValueError("workflow secret must be disabled before deletion")

    def resolve(self, tenant_id: str, reference: str) -> ResolvedSecret:
        safe_ref = self._required(reference, "secret_ref")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT secret_id,name,ciphertext,fingerprint,version FROM workflow_secrets
                WHERE tenant_id=%s AND status='ACTIVE' AND (secret_id=%s OR name=%s)
                ORDER BY CASE WHEN secret_id=%s THEN 0 ELSE 1 END LIMIT 1""",
                (self._required(tenant_id, "tenant_id"), safe_ref, safe_ref, safe_ref),
            )
            row = cur.fetchone()
        if not row:
            raise WorkflowSecretNotFound("workflow secret is missing, disabled, or belongs to another tenant")
        value, key_slot = self._decrypt(str(row["ciphertext"]))
        return ResolvedSecret(
            secret_id=str(row["secret_id"]), name=str(row["name"]), value=value,
            fingerprint=str(row["fingerprint"]), version=int(row["version"]), key_slot=key_slot,
        )

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _fernet(self) -> Fernet:
        return self._fernets()[0][1]

    def _fernets(self) -> tuple[tuple[str, Fernet], ...]:
        primary = str(self.master_key or "").strip()
        if not primary:
            raise SecretVaultUnavailable("WORKFLOW_SECRET_MASTER_KEY is not configured")
        keys = (primary, *self.previous_master_keys)
        if len(set(keys)) != len(keys):
            raise SecretVaultUnavailable("workflow Secret master keys must be distinct")
        result: list[tuple[str, Fernet]] = []
        for index, key in enumerate(keys):
            slot = "primary" if index == 0 else f"previous:{index}"
            try:
                result.append((slot, Fernet(key.encode("ascii"))))
            except (ValueError, UnicodeEncodeError) as exc:
                label = "WORKFLOW_SECRET_MASTER_KEY" if index == 0 else "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"
                raise SecretVaultUnavailable(f"{label} contains an invalid Fernet key") from exc
        return tuple(result)

    def _decrypt(self, ciphertext: str) -> tuple[str, str]:
        try:
            encoded = str(ciphertext or "").encode("ascii", errors="strict")
        except UnicodeEncodeError as exc:
            raise SecretVaultUnavailable("workflow secret ciphertext is not valid Fernet data") from exc
        for slot, fernet in self._fernets():
            try:
                return fernet.decrypt(encoded).decode("utf-8"), slot
            except (InvalidToken, UnicodeDecodeError):
                continue
        raise SecretVaultUnavailable("workflow secret cannot be decrypted with the configured master keys")

    @staticmethod
    def validate_master_key(value: str, label: str = "workflow Secret master key") -> Fernet:
        try:
            return Fernet(str(value or "").strip().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretVaultUnavailable(f"{label} must be a valid Fernet key") from exc

    @classmethod
    def _name(cls, value: str) -> str:
        name = str(value or "").strip()
        if not cls.NAME_PATTERN.fullmatch(name):
            raise ValueError("secret name must match ^[A-Za-z][A-Za-z0-9._-]{1,127}$")
        return name

    @staticmethod
    def _value(value: str) -> str:
        secret = str(value or "")
        if not secret or len(secret) > 8192 or "\x00" in secret:
            raise ValueError("secret value must contain 1 to 8192 valid characters")
        return secret

    @staticmethod
    def _required(value: str, label: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{label} is required")
        return result

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


workflow_secret_store = WorkflowSecretStore()
