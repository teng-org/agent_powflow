import json
import os
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _postgres_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if dsn:
        return dsn
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "agentpowflow")
    user = os.getenv("POSTGRES_USER", "apf")
    password = os.getenv("POSTGRES_PASSWORD", "apf")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


class ApiPoolMongoStore:
    """
    Backward-compatible name.
    Storage has been migrated from MongoDB to PostgreSQL.
    """

    def __init__(self, namespace: str = "api_pool", dsn: str | None = None) -> None:
        self.dsn = (dsn or _postgres_dsn()).strip()
        self._initialized = False
        raw = (namespace or "api_pool").strip().lower()
        self.namespace = re.sub(r"[^a-z0-9_]", "_", raw) or "api_pool"
        self.settings_table = f"{self.namespace}_settings"
        self.endpoints_table = f"{self.namespace}_endpoints"

    def _connect(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        from retrieval.schema_migrations import require_database_current

        require_database_current(self.dsn)
        self._initialized = True

    def load(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self._ensure_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT default_endpoint_id, default_endpoint_ids
                FROM {self.settings_table}
                WHERE settings_key = 'global'
                """
            )
            row = cur.fetchone() or {}
            settings = {
                "default_endpoint_id": row.get("default_endpoint_id"),
                "default_endpoint_ids": row.get("default_endpoint_ids") or {},
                "_initialized": bool(row),
            }
            cur.execute(f"SELECT payload FROM {self.endpoints_table} ORDER BY created_at ASC")
            endpoints = [dict(r.get("payload") or {}) for r in cur.fetchall()]
            return settings, endpoints

    def save_snapshot(self, settings: dict[str, Any], endpoints: list[dict[str, Any]]) -> None:
        self._ensure_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.settings_table} (settings_key, default_endpoint_id, default_endpoint_ids, updated_at)
                VALUES ('global', %s, %s::jsonb, NOW())
                ON CONFLICT (settings_key) DO UPDATE SET
                    default_endpoint_id = EXCLUDED.default_endpoint_id,
                    default_endpoint_ids = EXCLUDED.default_endpoint_ids,
                    updated_at = NOW()
                """,
                (
                    settings.get("default_endpoint_id"),
                    json.dumps(settings.get("default_endpoint_ids") or {}, ensure_ascii=False),
                ),
            )
            cur.execute(f"DELETE FROM {self.endpoints_table}")
            for endpoint in endpoints:
                cur.execute(
                    f"""
                    INSERT INTO {self.endpoints_table} (endpoint_id, payload, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (endpoint_id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (
                        endpoint["endpoint_id"],
                        json.dumps(endpoint, ensure_ascii=False),
                    ),
                )

    def upsert_endpoint(self, endpoint: dict[str, Any]) -> None:
        self._ensure_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.endpoints_table} (endpoint_id, payload, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (endpoint_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                (endpoint["endpoint_id"], json.dumps(endpoint, ensure_ascii=False)),
            )

    def delete_endpoint(self, endpoint_id: str) -> None:
        self._ensure_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.endpoints_table} WHERE endpoint_id = %s", (endpoint_id,))

    def update_defaults(self, default_endpoint_id: str | None, default_endpoint_ids: dict[str, str]) -> None:
        self._ensure_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.settings_table} (settings_key, default_endpoint_id, default_endpoint_ids, updated_at)
                VALUES ('global', %s, %s::jsonb, NOW())
                ON CONFLICT (settings_key) DO UPDATE SET
                    default_endpoint_id = EXCLUDED.default_endpoint_id,
                    default_endpoint_ids = EXCLUDED.default_endpoint_ids,
                    updated_at = NOW()
                """,
                (default_endpoint_id, json.dumps(default_endpoint_ids or {}, ensure_ascii=False)),
            )
