from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import uuid

import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import ApiException


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


@dataclass
class ChunkRecord:
    tenant_id: str
    kb_id: str
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str
    embedding: list[float]
    metadata: dict


class KnowledgeBaseStore:
    def __init__(self) -> None:
        self.dsn = _postgres_dsn()
        self.collection_prefix = os.getenv("QDRANT_COLLECTION_PREFIX", "apf")
        self.qdrant = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=(os.getenv("QDRANT_API_KEY", "") or None),
            timeout=30.0,
        )
        self._initialized = False

    def _conn(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _collection_name(self, tenant_id: str, kb_id: str) -> str:
        raw = f"{self.collection_prefix}_{tenant_id}_{kb_id}".lower()
        return re.sub(r"[^a-z0-9_]", "_", raw)

    def _strip_nul(self, value: str | None) -> str:
        return (value or "").replace("\x00", "")

    def _ensure_indexes(self) -> None:
        if self._initialized:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_bases (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, name TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ACTIVE',
                stats JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id))"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                filename TEXT NOT NULL, raw_text TEXT NOT NULL DEFAULT '',
                raw_text_preview TEXT NOT NULL DEFAULT '',
                text_size_bytes INTEGER NOT NULL DEFAULT 0, word_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'UPLOADED', chunk_count INTEGER NOT NULL DEFAULT 0,
                embedded_count INTEGER NOT NULL DEFAULT 0, embedding_dirty BOOLEAN NOT NULL DEFAULT TRUE,
                chunk_method TEXT NOT NULL DEFAULT 'sentence',
                chunk_size INTEGER NOT NULL DEFAULT 500,
                chunk_overlap INTEGER NOT NULL DEFAULT 50,
                chunk_delimiter TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id))"""
            )
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_method TEXT NOT NULL DEFAULT 'sentence'")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_size INTEGER NOT NULL DEFAULT 500")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_overlap INTEGER NOT NULL DEFAULT 50")
            cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_delimiter TEXT NOT NULL DEFAULT ''")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, text TEXT NOT NULL,
                embedding_json JSONB NOT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id, chunk_id))"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (tenant_id, kb_id, doc_id, chunk_index)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                task_type TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                filename TEXT NOT NULL DEFAULT '',
                progress_current INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0, progress_pct INTEGER NOT NULL DEFAULT 0,
                cancel_requested BOOLEAN NOT NULL DEFAULT FALSE, error_message TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS filename TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_current INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_total INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_pct INTEGER NOT NULL DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tenant_kb_updated ON tasks (tenant_id, kb_id, updated_at DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS task_events (
                event_id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, event_type TEXT NOT NULL, stage TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb, ts TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_ts ON task_events (task_id, ts DESC)")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS chunk_drafts (
                tenant_id TEXT NOT NULL, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
                action TEXT NOT NULL, text TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tenant_id, kb_id, doc_id, chunk_id))"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS system_settings (
                settings_key TEXT PRIMARY KEY, value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_by TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
        self._initialized = True

    def ensure_kb(self, tenant_id: str, kb_id: str, name: str | None = None) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id, kb_id, name, summary, status, stats, created_at, updated_at)
                VALUES (%s, %s, %s, '', 'ACTIVE', '{}'::jsonb, NOW(), NOW())
                ON CONFLICT (tenant_id, kb_id) DO NOTHING""",
                (tenant_id, kb_id, name or kb_id),
            )

    def get_kb(self, tenant_id: str, kb_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,summary,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s",
                (tenant_id, kb_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_kbs(self, tenant_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,name,summary,status,stats,created_at,updated_at FROM knowledge_bases WHERE tenant_id=%s ORDER BY created_at ASC",
                (tenant_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def _resolve_unique_kb_name(self, tenant_id: str, requested_name: str, exclude_kb_id: str | None = None) -> str:
        self._ensure_indexes()
        base_name = (requested_name or "").strip() or "未命名知识库"
        with self._conn() as conn, conn.cursor() as cur:
            if exclude_kb_id:
                cur.execute("SELECT name FROM knowledge_bases WHERE tenant_id=%s AND kb_id<>%s", (tenant_id, exclude_kb_id))
            else:
                cur.execute("SELECT name FROM knowledge_bases WHERE tenant_id=%s", (tenant_id,))
            existing = {str(row["name"] or "").strip().lower() for row in cur.fetchall() if str(row["name"] or "").strip()}
        if base_name.lower() not in existing:
            return base_name
        stem = re.sub(r"\s\(\d+\)$", "", base_name).strip() or "未命名知识库"
        idx = 2
        while True:
            candidate = f"{stem} ({idx})"
            if candidate.lower() not in existing:
                return candidate
            idx += 1

    def create_kb(self, tenant_id: str, kb_id: str, name: str, summary: str = "") -> dict:
        self._ensure_indexes()
        final_name = self._resolve_unique_kb_name(tenant_id, name)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge_bases (tenant_id,kb_id,name,summary,status,stats,created_at,updated_at)
                VALUES (%s,%s,%s,%s,'ACTIVE','{}'::jsonb,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id) DO UPDATE SET
                    name=EXCLUDED.name, summary=EXCLUDED.summary, updated_at=NOW()""",
                (tenant_id, kb_id, final_name, summary or ""),
            )
        self.recompute_kb_stats(tenant_id, kb_id)
        item = self.get_kb(tenant_id, kb_id)
        if item:
            item["name_adjusted"] = final_name != name
        return item or {"tenant_id": tenant_id, "kb_id": kb_id, "name": final_name, "summary": summary or "", "name_adjusted": final_name != name}

    def rename_kb(self, tenant_id: str, kb_id: str, name: str) -> dict | None:
        self._ensure_indexes()
        final_name = self._resolve_unique_kb_name(tenant_id, name, exclude_kb_id=kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE knowledge_bases SET name=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s", (final_name, tenant_id, kb_id))
            if cur.rowcount == 0:
                return None
        item = self.get_kb(tenant_id, kb_id)
        if item:
            item["name_adjusted"] = final_name != name
        return item

    def delete_kb(self, tenant_id: str, kb_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM task_events WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM tasks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            cur.execute("DELETE FROM knowledge_bases WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            deleted = cur.rowcount > 0
        collection_name = self._collection_name(tenant_id, kb_id)
        if self.qdrant.collection_exists(collection_name=collection_name):
            self.qdrant.delete_collection(collection_name=collection_name)
        return deleted

    def save_document(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        filename: str,
        raw_text: str,
        chunk_method: str = "sentence",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        chunk_delimiter: str | None = None,
    ) -> None:
        self._ensure_indexes()
        self.ensure_kb(tenant_id, kb_id)
        filename = self._strip_nul(filename)
        raw_text = self._strip_nul(raw_text)
        chunk_method = (chunk_method or "sentence").strip().lower()
        chunk_size = int(chunk_size or 500)
        chunk_overlap = int(chunk_overlap or 50)
        chunk_delimiter = self._strip_nul(chunk_delimiter)
        text_bytes = len(raw_text.encode("utf-8"))
        raw_text_to_store = raw_text
        raw_text_preview = raw_text[:2000]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (
                    tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,text_size_bytes,word_count,
                    status,chunk_count,embedded_count,embedding_dirty,chunk_method,chunk_size,chunk_overlap,chunk_delimiter,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'UPLOADED',0,0,TRUE,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id) DO UPDATE SET
                    filename=EXCLUDED.filename, raw_text=EXCLUDED.raw_text, raw_text_preview=EXCLUDED.raw_text_preview,
                    text_size_bytes=EXCLUDED.text_size_bytes, word_count=EXCLUDED.word_count,
                    status='UPLOADED', chunk_count=0, embedded_count=0, embedding_dirty=TRUE,
                    chunk_method=EXCLUDED.chunk_method, chunk_size=EXCLUDED.chunk_size,
                    chunk_overlap=EXCLUDED.chunk_overlap, chunk_delimiter=EXCLUDED.chunk_delimiter,
                    updated_at=NOW()""",
                (
                    tenant_id,
                    kb_id,
                    doc_id,
                    filename,
                    raw_text_to_store,
                    raw_text_preview,
                    text_bytes,
                    len(raw_text.split()),
                    chunk_method,
                    chunk_size,
                    chunk_overlap,
                    chunk_delimiter or "",
                ),
            )
        self.recompute_kb_stats(tenant_id, kb_id)

    def set_document_chunk_config(
        self,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        chunk_method: str,
        chunk_size: int,
        chunk_overlap: int,
        chunk_delimiter: str | None = None,
    ) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET chunk_method=%s, chunk_size=%s, chunk_overlap=%s, chunk_delimiter=%s, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (
                    (chunk_method or "sentence").strip().lower(),
                    int(chunk_size or 500),
                    int(chunk_overlap or 50),
                    self._strip_nul(chunk_delimiter),
                    tenant_id,
                    kb_id,
                    doc_id,
                ),
            )

    def get_document(self, tenant_id: str, kb_id: str, doc_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,raw_text,raw_text_preview,text_size_bytes,word_count,
                status,chunk_count,embedded_count,embedding_dirty,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at
                FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (tenant_id, kb_id, doc_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def set_document_status(self, tenant_id: str, kb_id: str, doc_id: str, status: str) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (status, tenant_id, kb_id, doc_id),
            )

    def mark_document_failed(self, tenant_id: str, kb_id: str, doc_id: str) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status='FAILED', embedding_dirty=TRUE, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (tenant_id, kb_id, doc_id),
            )

    def _update_document_counters(self, tenant_id: str, kb_id: str, doc_id: str, chunk_count: int, embedded_count: int, embedding_dirty: bool) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET chunk_count=%s, embedded_count=%s, embedding_dirty=%s, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                (int(chunk_count), int(embedded_count), bool(embedding_dirty), tenant_id, kb_id, doc_id),
            )

    def set_document_chunking_preview(self, tenant_id: str, kb_id: str, doc_id: str, chunk_count: int) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE documents
                SET status='CHUNKING', chunk_count=%s, embedded_count=0, embedding_dirty=TRUE, updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (int(chunk_count), tenant_id, kb_id, doc_id),
            )

    def _ensure_collection(self, collection_name: str, vector_size: int) -> None:
        if self.qdrant.collection_exists(collection_name=collection_name):
            current_size = self._get_collection_vector_size(collection_name)
            if current_size is None or current_size == int(vector_size):
                return
            # Auto-migrate collection dimension when embedding model changes.
            self.qdrant.delete_collection(collection_name=collection_name)
        try:
            self.qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
            )
        except ApiException:
            if not self.qdrant.collection_exists(collection_name=collection_name):
                raise

    def _get_collection_vector_size(self, collection_name: str) -> int | None:
        try:
            info = self.qdrant.get_collection(collection_name=collection_name)
        except Exception:
            return None

        # qdrant-client object path: info.config.params.vectors
        vectors_cfg = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
        if vectors_cfg is None and isinstance(info, dict):
            vectors_cfg = (
                (((info.get("config") or {}).get("params") or {}).get("vectors"))
                if isinstance(info.get("config"), dict)
                else None
            )

        if vectors_cfg is None:
            return None
        if hasattr(vectors_cfg, "size"):
            try:
                return int(getattr(vectors_cfg, "size"))
            except Exception:
                return None
        if isinstance(vectors_cfg, dict):
            if "size" in vectors_cfg:
                try:
                    return int(vectors_cfg.get("size"))
                except Exception:
                    return None
            for item in vectors_cfg.values():
                if hasattr(item, "size"):
                    try:
                        return int(getattr(item, "size"))
                    except Exception:
                        continue
                if isinstance(item, dict) and "size" in item:
                    try:
                        return int(item.get("size"))
                    except Exception:
                        continue
        return None

    def replace_chunks(self, tenant_id: str, kb_id: str, doc_id: str, chunks: list[ChunkRecord]) -> None:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            for c in chunks:
                safe_text = self._strip_nul(c.text)
                safe_filename = self._strip_nul((c.metadata or {}).get("filename", "unknown"))
                cur.execute(
                    """INSERT INTO chunks (tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,embedding_json,metadata,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,NOW(),NOW())""",
                    (
                        c.tenant_id,
                        c.kb_id,
                        c.doc_id,
                        c.chunk_id,
                        c.chunk_index,
                        safe_text,
                        json.dumps(c.embedding, ensure_ascii=False),
                        json.dumps({"filename": safe_filename}, ensure_ascii=False),
                    ),
                )
        if chunks:
            self._ensure_collection(collection_name, len(chunks[0].embedding))
            self.qdrant.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                            qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
            )
            self.qdrant.upsert(
                collection_name=collection_name,
                points=[
                    qmodels.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),
                        vector=c.embedding,
                        payload={
                            "tenant_id": c.tenant_id,
                            "kb_id": c.kb_id,
                            "doc_id": c.doc_id,
                            "chunk_id": c.chunk_id,
                            "chunk_index": c.chunk_index,
                            "text": self._strip_nul(c.text),
                            "source": self._strip_nul((c.metadata or {}).get("filename", "unknown")),
                        },
                    )
                    for c in chunks
                ],
            )
        self._update_document_counters(tenant_id, kb_id, doc_id, len(chunks), len(chunks), False)
        self.recompute_kb_stats(tenant_id, kb_id)

    def search_chunks(self, tenant_id: str, kb_id: str, query_vector: list[float], top_k: int, doc_ids: list[str] | None = None) -> list[dict]:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        conditions = [
            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
        ]
        if doc_ids:
            conditions.append(qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=doc_ids)))
        results = self.qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=qmodels.Filter(must=conditions),
            limit=max(1, min(int(top_k), 50)),
            with_payload=True,
        )
        return [
            {
                "doc_id": (hit.payload or {}).get("doc_id", ""),
                "chunk_id": (hit.payload or {}).get("chunk_id", ""),
                "chunk_index": (hit.payload or {}).get("chunk_index", 0),
                "text": (hit.payload or {}).get("text", ""),
                "score": float(hit.score),
                "source": (hit.payload or {}).get("source", "unknown"),
            }
            for hit in results
        ]

    def list_documents(self, tenant_id: str, kb_id: str, limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 500))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,filename,status,chunk_count,embedded_count,embedding_dirty,
                chunk_method,chunk_size,chunk_overlap,chunk_delimiter,
                created_at,updated_at,text_size_bytes,word_count
                FROM documents WHERE tenant_id=%s AND kb_id=%s ORDER BY updated_at DESC LIMIT %s""",
                (tenant_id, kb_id, cap),
            )
            docs = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT doc_id,COUNT(*)::int AS cnt FROM chunks WHERE tenant_id=%s AND kb_id=%s GROUP BY doc_id",
                (tenant_id, kb_id),
            )
            counts = {row["doc_id"]: int(row["cnt"]) for row in cur.fetchall()}
        for doc in docs:
            actual = counts.get(doc["doc_id"], 0)
            status = str(doc.get("status") or "").upper()
            # During active pipeline stages, keep preview counters from documents table.
            # Chunks table may still hold previous generation until indexing replacement finishes.
            if status in {"PARSING", "CHUNKING", "EMBEDDING", "INDEXING", "UPLOADING"}:
                continue
            if int(doc.get("chunk_count") or 0) != actual or int(doc.get("embedded_count") or 0) != actual:
                self._update_document_counters(tenant_id, kb_id, doc["doc_id"], actual, actual, False)
                doc["chunk_count"] = actual
                doc["embedded_count"] = actual
                doc["embedding_dirty"] = False
        return docs

    def list_chunks(self, tenant_id: str, kb_id: str, doc_id: str, limit: int = 200) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,doc_id,chunk_id,chunk_index,text,metadata,updated_at,created_at
                FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC LIMIT %s""",
                (tenant_id, kb_id, doc_id, cap),
            )
            return [dict(row) for row in cur.fetchall()]

    def build_document_text(self, tenant_id: str, kb_id: str, doc_id: str) -> str:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT text FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY chunk_index ASC", (tenant_id, kb_id, doc_id))
            parts = [str(row["text"] or "") for row in cur.fetchall()]
        return "\n".join([p for p in parts if p])

    def delete_document(self, tenant_id: str, kb_id: str, doc_id: str) -> bool:
        self._ensure_indexes()
        collection_name = self._collection_name(tenant_id, kb_id)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            deleted = cur.rowcount > 0
            cur.execute("DELETE FROM chunks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM tasks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
            cur.execute("DELETE FROM task_events WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
        if self.qdrant.collection_exists(collection_name=collection_name):
            self.qdrant.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                            qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                            qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
            )
        self.recompute_kb_stats(tenant_id, kb_id)
        return deleted

    def recompute_kb_stats(self, tenant_id: str, kb_id: str) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*)::int AS document_count,
                COALESCE(SUM(text_size_bytes),0)::bigint AS total_size_bytes,
                COALESCE(SUM(word_count),0)::bigint AS total_words
                FROM documents WHERE tenant_id=%s AND kb_id=%s""",
                (tenant_id, kb_id),
            )
            doc_stats = cur.fetchone() or {"document_count": 0, "total_size_bytes": 0, "total_words": 0}
            cur.execute("SELECT COUNT(*)::int AS total_chunks FROM chunks WHERE tenant_id=%s AND kb_id=%s", (tenant_id, kb_id))
            chunk_stats = cur.fetchone() or {"total_chunks": 0}
            stats = {
                "document_count": int(doc_stats.get("document_count") or 0),
                "total_size_bytes": int(doc_stats.get("total_size_bytes") or 0),
                "total_chunks": int(chunk_stats.get("total_chunks") or 0),
                "total_words": int(doc_stats.get("total_words") or 0),
            }
            cur.execute(
                "UPDATE knowledge_bases SET stats=%s::jsonb, updated_at=NOW() WHERE tenant_id=%s AND kb_id=%s",
                (json.dumps(stats, ensure_ascii=False), tenant_id, kb_id),
            )
        return stats

    def create_task_record(
        self,
        task_id: str,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        task_type: str,
        stage: str,
        filename: str | None = None,
    ) -> None:
        self._ensure_indexes()
        safe_filename = self._strip_nul(filename)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (task_id,tenant_id,kb_id,doc_id,task_type,status,stage,filename,progress_current,progress_total,progress_pct,cancel_requested,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'PENDING',%s,%s,0,0,0,FALSE,NOW(),NOW())
                ON CONFLICT (task_id) DO UPDATE SET
                    tenant_id=EXCLUDED.tenant_id,kb_id=EXCLUDED.kb_id,doc_id=EXCLUDED.doc_id,
                    task_type=EXCLUDED.task_type,status='PENDING',stage=EXCLUDED.stage,
                    filename=COALESCE(NULLIF(EXCLUDED.filename, ''), tasks.filename),
                    progress_current=0,progress_total=0,progress_pct=0,
                    cancel_requested=FALSE,updated_at=NOW()""",
                (task_id, tenant_id, kb_id, doc_id, task_type, stage, safe_filename),
            )
        self.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CREATED", stage, {"task_type": task_type})

    def update_task_record(self, task_id: str, status: str, stage: str, error_message: str | None = None) -> None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET status=%s, stage=%s, error_message=%s,
                    progress_current=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') THEN progress_total ELSE progress_current END,
                    progress_pct=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED') AND progress_total > 0 THEN 100 ELSE progress_pct END,
                    updated_at=NOW()
                WHERE task_id=%s""",
                (status, stage, error_message, status, status, task_id),
            )
        self.append_task_event(task_id, "", "", "", "TASK_STATUS", stage, {"status": status, "error_message": error_message})

    def update_task_progress(
        self,
        task_id: str,
        stage: str,
        current: int,
        total: int,
        progress_pct: int | None = None,
    ) -> None:
        self._ensure_indexes()
        c = max(0, int(current or 0))
        t = max(0, int(total or 0))
        if progress_pct is None:
            pct = int((c * 100 / t)) if t > 0 else 0
        else:
            pct = max(0, min(100, int(progress_pct)))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                SET stage=%s, progress_current=%s, progress_total=%s, progress_pct=%s, updated_at=NOW()
                WHERE task_id=%s""",
                (stage, c, t, pct, task_id),
            )

    def request_cancel_task(self, task_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE tasks SET cancel_requested=TRUE,status='CANCELLING',updated_at=NOW() WHERE task_id=%s", (task_id,))
            matched = cur.rowcount > 0
        if matched:
            rec = self.get_task(task_id) or {}
            self.append_task_event(task_id, rec.get("tenant_id", ""), rec.get("kb_id", ""), rec.get("doc_id", ""), "CANCEL_REQUESTED", "CANCELLING", {})
        return matched

    def is_cancel_requested(self, task_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT cancel_requested FROM tasks WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
            return bool(row and row.get("cancel_requested"))

    def list_tasks(self, tenant_id: str, kb_id: str | None = None, limit: int = 100) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 300))
        with self._conn() as conn, conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                    COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                    progress_current,progress_total,progress_pct,
                    cancel_requested,error_message,tasks.created_at,tasks.updated_at
                    FROM tasks
                    LEFT JOIN documents
                    ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                    WHERE tasks.tenant_id=%s AND tasks.kb_id=%s ORDER BY tasks.updated_at DESC LIMIT %s""",
                    (tenant_id, kb_id, cap),
                )
            else:
                cur.execute(
                    """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                    COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                    progress_current,progress_total,progress_pct,
                    cancel_requested,error_message,tasks.created_at,tasks.updated_at
                    FROM tasks
                    LEFT JOIN documents
                    ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                    WHERE tasks.tenant_id=%s ORDER BY tasks.updated_at DESC LIMIT %s""",
                    (tenant_id, cap),
                )
            return [dict(row) for row in cur.fetchall()]

    def clear_finished_tasks(self, tenant_id: str, kb_id: str | None = None) -> dict:
        self._ensure_indexes()
        terminal_statuses = ("SUCCEEDED", "FAILED", "CANCELLED")
        with self._conn() as conn, conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """SELECT task_id FROM tasks
                    WHERE tenant_id=%s AND kb_id=%s AND status = ANY(%s)""",
                    (tenant_id, kb_id, list(terminal_statuses)),
                )
            else:
                cur.execute(
                    """SELECT task_id FROM tasks
                    WHERE tenant_id=%s AND status = ANY(%s)""",
                    (tenant_id, list(terminal_statuses)),
                )
            task_ids = [str(row["task_id"]) for row in cur.fetchall()]
            if not task_ids:
                return {"deleted_tasks": 0, "deleted_events": 0}

            cur.execute("DELETE FROM task_events WHERE task_id = ANY(%s)", (task_ids,))
            deleted_events = int(cur.rowcount or 0)
            cur.execute("DELETE FROM tasks WHERE task_id = ANY(%s)", (task_ids,))
            deleted_tasks = int(cur.rowcount or 0)
            return {"deleted_tasks": deleted_tasks, "deleted_events": deleted_events}

    def get_task(self, task_id: str) -> dict | None:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tasks.task_id,tasks.tenant_id,tasks.kb_id,tasks.doc_id,tasks.task_type,tasks.status,tasks.stage,
                COALESCE(NULLIF(tasks.filename, ''), documents.filename, '') AS filename,
                progress_current,progress_total,progress_pct,
                cancel_requested,error_message,tasks.created_at,tasks.updated_at
                FROM tasks
                LEFT JOIN documents
                ON documents.tenant_id=tasks.tenant_id AND documents.kb_id=tasks.kb_id AND documents.doc_id=tasks.doc_id
                WHERE tasks.task_id=%s""",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def append_task_event(self, task_id: str, tenant_id: str, kb_id: str, doc_id: str, event_type: str, stage: str, payload: dict) -> None:
        self._ensure_indexes()
        if not tenant_id or not kb_id or not doc_id:
            rec = self.get_task(task_id) or {}
            tenant_id = tenant_id or rec.get("tenant_id", "")
            kb_id = kb_id or rec.get("kb_id", "")
            doc_id = doc_id or rec.get("doc_id", "")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO task_events (task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())""",
                (task_id, tenant_id, kb_id, doc_id, event_type, stage, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def list_task_events(self, task_id: str, limit: int = 200) -> list[dict]:
        self._ensure_indexes()
        cap = max(1, min(int(limit), 1000))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                FROM (
                    SELECT task_id,tenant_id,kb_id,doc_id,event_type,stage,payload,ts
                    FROM task_events
                    WHERE task_id=%s
                    ORDER BY ts DESC
                    LIMIT %s
                ) t
                ORDER BY ts ASC""",
                (task_id, cap),
            )
            return [dict(row) for row in cur.fetchall()]

    def find_active_doc_task_ids(self, tenant_id: str, kb_id: str, doc_id: str) -> list[str]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT task_id FROM tasks WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND status IN ('PENDING','RUNNING','CANCELLING')",
                (tenant_id, kb_id, doc_id),
            )
            return [str(row["task_id"]) for row in cur.fetchall()]

    def set_chunk_draft(self, tenant_id: str, kb_id: str, doc_id: str, chunk_id: str, action: str, text: str | None = None) -> None:
        self._ensure_indexes()
        text = self._strip_nul(text)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO chunk_drafts (tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (tenant_id,kb_id,doc_id,chunk_id) DO UPDATE SET
                    action=EXCLUDED.action,text=EXCLUDED.text,updated_at=NOW()""",
                (tenant_id, kb_id, doc_id, chunk_id, action, text),
            )

    def clear_chunk_draft(self, tenant_id: str, kb_id: str, doc_id: str, chunk_id: str) -> bool:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s AND chunk_id=%s", (tenant_id, kb_id, doc_id, chunk_id))
            return cur.rowcount > 0

    def list_chunk_drafts(self, tenant_id: str, kb_id: str, doc_id: str) -> list[dict]:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id,kb_id,doc_id,chunk_id,action,text,created_at,updated_at FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s ORDER BY updated_at DESC",
                (tenant_id, kb_id, doc_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def commit_chunk_drafts(self, tenant_id: str, kb_id: str, doc_id: str, embed_fn) -> dict:
        self._ensure_indexes()
        chunks = self.list_chunks(tenant_id, kb_id, doc_id, limit=10000)
        drafts = {item["chunk_id"]: item for item in self.list_chunk_drafts(tenant_id, kb_id, doc_id)}
        if not drafts:
            return {"doc_id": doc_id, "total_chunks": len(chunks), "reembedded_chunks": 0, "deleted_chunks": 0}

        kept: list[ChunkRecord] = []
        reembedded = 0
        deleted = 0
        for idx, chunk in enumerate(chunks):
            draft = drafts.get(chunk["chunk_id"])
            if draft and draft.get("action") == "delete":
                deleted += 1
                continue
            text = chunk["text"]
            source = (chunk.get("metadata") or {}).get("filename", "unknown")
            if draft and draft.get("action") == "edit":
                text = draft.get("text") or text
                raw = f"{tenant_id}:{kb_id}:{doc_id}:{idx}:{text}"
                chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
                embedding = embed_fn(tenant_id, text)
                reembedded += 1
            else:
                chunk_id = chunk["chunk_id"]
                embedding = embed_fn(tenant_id, text)
            kept.append(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    chunk_index=len(kept),
                    text=text,
                    embedding=embedding,
                    metadata={"filename": source},
                )
            )
        self.replace_chunks(tenant_id, kb_id, doc_id, kept)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s", (tenant_id, kb_id, doc_id))
        self.recompute_kb_stats(tenant_id, kb_id)
        return {"doc_id": doc_id, "total_chunks": len(kept), "reembedded_chunks": reembedded, "deleted_chunks": deleted}

    def get_upload_settings(self) -> dict:
        self._ensure_indexes()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value_json FROM system_settings WHERE settings_key='upload_limits'")
            row = cur.fetchone() or {}
            value = row.get("value_json") or {}
            max_file_size_mb = int(value.get("max_file_size_mb", 20))
            if max_file_size_mb < 1:
                max_file_size_mb = 1
            return {"max_file_size_mb": max_file_size_mb}

    def set_upload_settings(self, max_file_size_mb: int, actor_id: str) -> dict:
        self._ensure_indexes()
        payload = {"max_file_size_mb": int(max_file_size_mb)}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_settings (settings_key,value_json,updated_by,created_at,updated_at)
                VALUES ('upload_limits',%s::jsonb,%s,NOW(),NOW())
                ON CONFLICT (settings_key) DO UPDATE SET
                    value_json=EXCLUDED.value_json, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                (json.dumps(payload, ensure_ascii=False), actor_id),
            )
        return self.get_upload_settings()


kb_store = KnowledgeBaseStore()
