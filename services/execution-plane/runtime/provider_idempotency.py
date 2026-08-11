import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class ProviderInvocationConflict(RuntimeError):
    pass


class ProviderInvocationInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class InvocationClaim:
    invocation_key: str
    owner_token: str
    cached_response: dict | None


def canonical_hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_invocation_key(
    *, tenant_id: str, run_id: str, workflow_checksum: str, node_id: str, operation: str
) -> str:
    semantic_identity = "\n".join([tenant_id, run_id, workflow_checksum, node_id, operation])
    return hashlib.sha256(semantic_identity.encode("utf-8")).hexdigest()


class ProviderInvocationStore:
    def __init__(self, dsn: str | None = None, claim_seconds: int = 300) -> None:
        self.dsn = dsn or os.getenv("POSTGRES_DSN", "postgresql://apf:apf@localhost:5432/agentpowflow")
        self.claim_seconds = max(30, int(claim_seconds))

    def claim(
        self,
        *,
        invocation_key: str,
        request_hash: str,
        tenant_id: str,
        run_id: str,
        workflow_checksum: str,
        node_id: str,
        operation: str,
    ) -> InvocationClaim:
        import psycopg
        from psycopg.rows import dict_row

        owner_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.claim_seconds)
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO provider_invocations (
                        invocation_key, request_hash, tenant_id, run_id, workflow_checksum,
                        node_id, operation, status, owner_token, claim_until, attempt_count,
                        response_json, last_error, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'IN_PROGRESS',%s,%s,1,'{}'::jsonb,'',%s,%s)
                    ON CONFLICT (invocation_key) DO NOTHING
                    """,
                    (
                        invocation_key, request_hash, tenant_id, run_id, workflow_checksum,
                        node_id, operation, owner_token, lease_until, now, now,
                    ),
                )
                inserted = cur.rowcount == 1
                cur.execute(
                    "SELECT * FROM provider_invocations WHERE invocation_key=%s FOR UPDATE",
                    (invocation_key,),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("provider invocation claim disappeared")
                if row["request_hash"] != request_hash:
                    raise ProviderInvocationConflict("stable invocation key was reused with a different request")
                if inserted:
                    return InvocationClaim(invocation_key, owner_token, None)
                if row["status"] == "SUCCEEDED":
                    cached = row.get("response_json") or {}
                    return InvocationClaim(invocation_key, "", cached if isinstance(cached, dict) else {})
                if (
                    row["status"] == "IN_PROGRESS"
                    and row["owner_token"] != owner_token
                    and row["claim_until"] is not None
                    and row["claim_until"] > now
                ):
                    raise ProviderInvocationInProgress("provider invocation is owned by another live attempt")
                cur.execute(
                    """
                    UPDATE provider_invocations
                    SET status='IN_PROGRESS', owner_token=%s, claim_until=%s,
                        attempt_count=attempt_count+1, last_error='', updated_at=%s
                    WHERE invocation_key=%s
                    """,
                    (owner_token, lease_until, now, invocation_key),
                )
        return InvocationClaim(invocation_key, owner_token, None)

    def succeed(self, claim: InvocationClaim, response: dict) -> None:
        if not claim.owner_token:
            return
        self._finish(claim, "SUCCEEDED", response, "")

    def fail(self, claim: InvocationClaim, error: str) -> None:
        if not claim.owner_token:
            return
        self._finish(claim, "FAILED", {}, str(error or "")[:2000])

    def _finish(self, claim: InvocationClaim, status: str, response: dict, error: str) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE provider_invocations
                    SET status=%s, response_json=%s::jsonb, last_error=%s,
                        owner_token='', claim_until=NULL, completed_at=now(), updated_at=now()
                    WHERE invocation_key=%s AND owner_token=%s AND status='IN_PROGRESS'
                    """,
                    (status, json.dumps(response, ensure_ascii=False), error, claim.invocation_key, claim.owner_token),
                )
                if cur.rowcount != 1:
                    raise ProviderInvocationConflict("provider invocation ownership was lost before completion")


provider_invocation_store = ProviderInvocationStore()
