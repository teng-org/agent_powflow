from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from qdrant_client.http import models as qmodels

from retrieval.store import KnowledgeBaseStore, kb_store


class IndexLifecycleError(RuntimeError):
    pass


class IndexQualityGateFailed(IndexLifecycleError):
    pass


class RagIndexLifecycle:
    REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

    def __init__(self, store: KnowledgeBaseStore | None = None) -> None:
        self.store = store or kb_store

    def _revision(self, value: str) -> str:
        revision = str(value or "").strip()
        if not self.REVISION_RE.fullmatch(revision):
            raise ValueError("index revision must use 1-80 letters, numbers, dot, underscore, or hyphen")
        return revision

    def list_manifests(self, tenant_id: str, kb_id: str) -> list[dict]:
        self.store._ensure_indexes()
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id,kb_id,index_revision,status,parser_id,chunker_id,dense_model_id,
                    sparse_model_id,reranker_id,config_json,document_count,chunk_count,
                    lexical_chunk_count,collection_name,source_revision,build_id,gate_status,
                    quality_report,build_error,built_at,activated_at,activated_by,created_at,updated_at
                FROM rag_index_manifests WHERE tenant_id=%s AND kb_id=%s
                ORDER BY created_at DESC,index_revision DESC""",
                (tenant_id, kb_id),
            )
            return [dict(row) for row in cur.fetchall()]

    def _snapshot_source(self, tenant_id: str, kb_id: str, revision: str) -> dict:
        with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (tenant_id, kb_id, revision),
            )
            cur.execute(
                "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (tenant_id, kb_id, revision),
            )
            cur.execute(
                """INSERT INTO rag_index_chunks
                (tenant_id,kb_id,index_revision,doc_id,chunk_id,chunk_index,text,embedding_json,
                 metadata,lexical_terms,lexical_tf,lexical_token_count,parent_id,section_path,
                 block_type,page_start,page_end,frozen,created_at)
                SELECT c.tenant_id,c.kb_id,%s,c.doc_id,c.chunk_id,c.chunk_index,c.text,c.embedding_json,
                    c.metadata,c.lexical_terms,c.lexical_tf,c.lexical_token_count,c.parent_id,c.section_path,
                    c.block_type,c.page_start,c.page_end,c.frozen,NOW()
                FROM chunks c JOIN documents d
                  ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                WHERE c.tenant_id=%s AND c.kb_id=%s AND c.frozen=FALSE
                  AND d.frozen=FALSE AND d.status='INDEXED'""",
                (revision, tenant_id, kb_id),
            )
            cur.execute(
                """INSERT INTO rag_index_parents
                (tenant_id,kb_id,index_revision,doc_id,parent_id,parent_index,text,section_path,
                 page_start,page_end,metadata,created_at)
                SELECT p.tenant_id,p.kb_id,%s,p.doc_id,p.parent_id,p.parent_index,p.text,p.section_path,
                    p.page_start,p.page_end,p.metadata,NOW()
                FROM chunk_parents p JOIN documents d
                  ON d.tenant_id=p.tenant_id AND d.kb_id=p.kb_id AND d.doc_id=p.doc_id
                WHERE p.tenant_id=%s AND p.kb_id=%s AND d.frozen=FALSE AND d.status='INDEXED'""",
                (revision, tenant_id, kb_id),
            )
            cur.execute(
                """SELECT count(*)::int AS chunk_count,count(DISTINCT doc_id)::int AS document_count,
                    count(*) FILTER (WHERE jsonb_typeof(embedding_json)='array'
                        AND jsonb_array_length(embedding_json)>0)::int AS embedded_count,
                    count(*) FILTER (WHERE lexical_token_count>0)::int AS lexical_chunk_count,
                    count(*) FILTER (WHERE parent_id<>'')::int AS parent_linked_count
                FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                (tenant_id, kb_id, revision),
            )
            return dict(cur.fetchone() or {})

    def _snapshot_rows(self, tenant_id: str, kb_id: str, revision: str) -> list[dict]:
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT doc_id,chunk_id,chunk_index,text,embedding_json,metadata,parent_id,
                    section_path,block_type,page_start,page_end
                FROM rag_index_chunks
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s
                ORDER BY doc_id,chunk_index""",
                (tenant_id, kb_id, revision),
            )
            return [dict(row) for row in cur.fetchall()]

    def _publish_snapshot_collection(
        self,
        tenant_id: str,
        kb_id: str,
        revision: str,
        collection_name: str,
        *,
        batch_size: int = 16,
    ) -> int:
        rows = self._snapshot_rows(tenant_id, kb_id, revision)
        vector_size = next(
            (len(list(row.get("embedding_json") or [])) for row in rows if row.get("embedding_json")),
            0,
        )
        if vector_size <= 0:
            raise IndexQualityGateFailed("shadow index has no embedding vectors")
        if self.store.qdrant.collection_exists(collection_name=collection_name):
            self.store.qdrant.delete_collection(collection_name=collection_name)
        self.store._ensure_collection(collection_name, vector_size)
        published = 0
        size = max(8, min(int(batch_size or 16), 256))
        for offset in range(0, len(rows), size):
            points = []
            for row in rows[offset : offset + size]:
                vector = [float(value) for value in (row.get("embedding_json") or [])]
                if len(vector) != vector_size:
                    raise IndexQualityGateFailed("shadow index contains incomplete or inconsistent embeddings")
                metadata = dict(row.get("metadata") or {})
                points.append(qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{revision}:{row['chunk_id']}")),
                    vector=vector,
                    payload={
                        "tenant_id": tenant_id,
                        "kb_id": kb_id,
                        "index_revision": revision,
                        "doc_id": row["doc_id"],
                        "chunk_id": row["chunk_id"],
                        "chunk_index": int(row.get("chunk_index") or 0),
                        "text": self.store._strip_nul(str(row.get("text") or "")),
                        "source": self.store._strip_nul(str(metadata.get("filename") or "unknown")),
                        "parent_id": str(row.get("parent_id") or ""),
                        "section_path": list(row.get("section_path") or []),
                        "block_type": str(row.get("block_type") or "paragraph"),
                        "page_start": int(row.get("page_start") or 0),
                        "page_end": int(row.get("page_end") or 0),
                    },
                ))
            self.store.qdrant.upsert(collection_name=collection_name, points=points)
            published += len(points)
        return published

    def bootstrap_active_snapshot(self, tenant_id: str, kb_id: str, actor_id: str = "system") -> dict:
        revision = self._revision(self.store.get_active_index_revision(tenant_id, kb_id))
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        with self.store._qdrant_collection_locks([collection_name]):
            return self._bootstrap_active_snapshot_locked(tenant_id, kb_id, actor_id)

    def _bootstrap_active_snapshot_locked(
        self,
        tenant_id: str,
        kb_id: str,
        actor_id: str = "system",
    ) -> dict:
        """Publish the current legacy source as a rollback-capable snapshot without changing its pointer."""
        self.store._ensure_indexes()
        revision = self._revision(self.store.get_active_index_revision(tenant_id, kb_id))
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        if (
            self.store.has_index_snapshot(tenant_id, kb_id, revision)
            and self.store.qdrant.collection_exists(collection_name=collection_name)
        ):
            return self.store.get_rag_index_manifest(tenant_id, kb_id, revision)
        counts = self._snapshot_source(tenant_id, kb_id, revision)
        try:
            published = self._publish_snapshot_collection(tenant_id, kb_id, revision, collection_name)
            report = self._integrity_report(counts, published)
            report["mode"] = "active-bootstrap"
            report["snapshot_ready"] = bool(report["passed"])
            if not report["passed"]:
                raise IndexQualityGateFailed("active snapshot integrity gate failed")
            with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"rag-index:{tenant_id}:{kb_id}",))
                cur.execute(
                    """SELECT active_index_revision FROM knowledge_bases
                    WHERE tenant_id=%s AND kb_id=%s FOR UPDATE""",
                    (tenant_id, kb_id),
                )
                current = cur.fetchone()
                if not current or str(current.get("active_index_revision") or "") != revision:
                    raise IndexLifecycleError("active index changed while its snapshot was building")
                cur.execute(
                    """INSERT INTO rag_index_manifests
                    (tenant_id,kb_id,index_revision,status,parser_id,chunker_id,dense_model_id,
                     sparse_model_id,reranker_id,config_json,document_count,chunk_count,
                     lexical_chunk_count,collection_name,source_revision,build_id,gate_status,
                     quality_report,build_error,built_at,activated_at,activated_by,created_at,updated_at)
                    VALUES (%s,%s,%s,'ACTIVE','structured-text-v2','parent-child-v2',
                            'configured-embedding','lexical-ngram-v1','BAAI/bge-reranker-v2-m3',
                            %s::jsonb,%s,%s,%s,%s,%s,%s,'PASSED',%s::jsonb,'',NOW(),NOW(),%s,NOW(),NOW())
                    ON CONFLICT (tenant_id,kb_id,index_revision) DO UPDATE SET
                        status='ACTIVE',document_count=EXCLUDED.document_count,
                        chunk_count=EXCLUDED.chunk_count,lexical_chunk_count=EXCLUDED.lexical_chunk_count,
                        collection_name=EXCLUDED.collection_name,gate_status='PASSED',
                        quality_report=EXCLUDED.quality_report,build_error='',built_at=NOW(),
                        activated_by=EXCLUDED.activated_by,updated_at=NOW()""",
                    (
                        tenant_id, kb_id, revision, json.dumps({"bootstrap": True}),
                        int(counts.get("document_count") or 0), int(counts.get("chunk_count") or 0),
                        int(counts.get("lexical_chunk_count") or 0), collection_name, revision,
                        str(uuid.uuid4()), json.dumps(report, ensure_ascii=False), str(actor_id or "system"),
                    ),
                )
            return self.store.get_rag_index_manifest(tenant_id, kb_id, revision)
        except Exception:
            if self.store.qdrant.collection_exists(collection_name=collection_name):
                self.store.qdrant.delete_collection(collection_name=collection_name)
            with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                    (tenant_id, kb_id, revision),
                )
                cur.execute(
                    "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                    (tenant_id, kb_id, revision),
                )
            raise

    def sync_document_to_active(self, tenant_id: str, kb_id: str, doc_id: str) -> dict:
        """Publish one indexed document into the active immutable-generation namespace."""
        revision = self.store.get_active_index_revision(tenant_id, kb_id)
        if not self.store.has_index_snapshot(tenant_id, kb_id, revision):
            return {"synced": False, "reason": "active_revision_is_legacy", "index_revision": revision}
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT c.doc_id,c.chunk_id,c.chunk_index,c.text,c.embedding_json,c.metadata,
                    c.lexical_terms,c.lexical_tf,c.lexical_token_count,c.parent_id,c.section_path,
                    c.block_type,c.page_start,c.page_end,c.frozen
                FROM chunks c JOIN documents d
                  ON d.tenant_id=c.tenant_id AND d.kb_id=c.kb_id AND d.doc_id=c.doc_id
                WHERE c.tenant_id=%s AND c.kb_id=%s AND c.doc_id=%s
                  AND c.frozen=FALSE AND d.frozen=FALSE AND d.status='INDEXED'
                ORDER BY c.chunk_index""",
                (tenant_id, kb_id, doc_id),
            )
            rows = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT doc_id,parent_id,parent_index,text,section_path,page_start,page_end,metadata
                FROM chunk_parents WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s
                ORDER BY parent_index""",
                (tenant_id, kb_id, doc_id),
            )
            parents = [dict(row) for row in cur.fetchall()]
        if not rows:
            raise IndexLifecycleError("indexed document has no publishable chunks")
        vector_size = len(list(rows[0].get("embedding_json") or []))
        if vector_size <= 0 or any(len(list(row.get("embedding_json") or [])) != vector_size for row in rows):
            raise IndexQualityGateFailed("indexed document has incomplete embeddings")
        if not self.store.qdrant.collection_exists(collection_name=collection_name):
            raise IndexLifecycleError("active revision collection is missing; refusing partial recreation")
        points = []
        for row in rows:
            metadata = dict(row.get("metadata") or {})
            points.append(qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{revision}:{row['chunk_id']}")),
                vector=[float(value) for value in row.get("embedding_json") or []],
                payload={
                    "tenant_id": tenant_id,
                    "kb_id": kb_id,
                    "index_revision": revision,
                    "doc_id": doc_id,
                    "chunk_id": row["chunk_id"],
                    "chunk_index": int(row.get("chunk_index") or 0),
                    "text": self.store._strip_nul(str(row.get("text") or "")),
                    "source": self.store._strip_nul(str(metadata.get("filename") or "unknown")),
                    "parent_id": str(row.get("parent_id") or ""),
                    "section_path": list(row.get("section_path") or []),
                    "block_type": str(row.get("block_type") or "paragraph"),
                    "page_start": int(row.get("page_start") or 0),
                    "page_end": int(row.get("page_end") or 0),
                },
            ))
        with self.store._qdrant_collection_locks([collection_name]):
            self.store._ensure_collection(collection_name, vector_size)
            self.store.qdrant.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(filter=qmodels.Filter(must=[
                    qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
                    qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id)),
                    qmodels.FieldCondition(key="index_revision", match=qmodels.MatchValue(value=revision)),
                    qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id)),
                ])),
            )
            self.store.qdrant.upsert(collection_name=collection_name, points=points)
        with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s AND doc_id=%s",
                (tenant_id, kb_id, revision, doc_id),
            )
            cur.execute(
                "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s AND doc_id=%s",
                (tenant_id, kb_id, revision, doc_id),
            )
            for row in rows:
                cur.execute(
                    """INSERT INTO rag_index_chunks
                    (tenant_id,kb_id,index_revision,doc_id,chunk_id,chunk_index,text,embedding_json,
                     metadata,lexical_terms,lexical_tf,lexical_token_count,parent_id,section_path,
                     block_type,page_start,page_end,frozen,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,NOW())""",
                    (
                        tenant_id, kb_id, revision, doc_id, row["chunk_id"], row["chunk_index"], row["text"],
                        json.dumps(row.get("embedding_json") or []), json.dumps(row.get("metadata") or {}),
                        list(row.get("lexical_terms") or []), json.dumps(row.get("lexical_tf") or {}),
                        int(row.get("lexical_token_count") or 0), str(row.get("parent_id") or ""),
                        list(row.get("section_path") or []), str(row.get("block_type") or "paragraph"),
                        int(row.get("page_start") or 0), int(row.get("page_end") or 0), bool(row.get("frozen")),
                    ),
                )
            for parent in parents:
                cur.execute(
                    """INSERT INTO rag_index_parents
                    (tenant_id,kb_id,index_revision,doc_id,parent_id,parent_index,text,section_path,
                     page_start,page_end,metadata,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())""",
                    (
                        tenant_id, kb_id, revision, doc_id, parent["parent_id"], parent["parent_index"],
                        parent["text"], list(parent.get("section_path") or []), int(parent.get("page_start") or 0),
                        int(parent.get("page_end") or 0), json.dumps(parent.get("metadata") or {}),
                    ),
                )
        self.store.refresh_rag_index_manifest(tenant_id, kb_id)
        return {"synced": True, "index_revision": revision, "chunk_count": len(rows)}

    @staticmethod
    def _integrity_report(counts: dict, published_points: int) -> dict:
        chunks = int(counts.get("chunk_count") or 0)
        documents = int(counts.get("document_count") or 0)
        embedded = int(counts.get("embedded_count") or 0)
        lexical = int(counts.get("lexical_chunk_count") or 0)
        parent_linked = int(counts.get("parent_linked_count") or 0)
        report = {
            "mode": "integrity",
            "document_count": documents,
            "chunk_count": chunks,
            "embedded_count": embedded,
            "lexical_chunk_count": lexical,
            "parent_linked_count": parent_linked,
            "published_point_count": int(published_points),
            "embedding_coverage": round(embedded / max(1, chunks), 6),
            "lexical_coverage": round(lexical / max(1, chunks), 6),
            "parent_coverage": round(parent_linked / max(1, chunks), 6),
        }
        checks = {
            "non_empty": chunks > 0 and documents > 0,
            "embedding_complete": embedded == chunks,
            "qdrant_complete": int(published_points) == chunks,
            "lexical_coverage": chunks > 0 and lexical / chunks >= 0.95,
            "parent_coverage": chunks > 0 and parent_linked / chunks >= 0.95,
        }
        report["checks"] = checks
        report["passed"] = all(checks.values())
        return report

    def build_shadow(
        self,
        tenant_id: str,
        kb_id: str,
        revision: str,
        *,
        parser_id: str = "structured-text-v2",
        chunker_id: str = "parent-child-v2",
        dense_model_id: str = "configured-embedding",
        sparse_model_id: str = "lexical-ngram-v1",
        reranker_id: str = "BAAI/bge-reranker-v2-m3",
        config: dict | None = None,
    ) -> dict:
        self.store._ensure_indexes()
        effective_config = dict(config or {})
        revision = self._revision(revision)
        kb = self.store.get_kb(tenant_id, kb_id)
        if not kb:
            raise IndexLifecycleError("knowledge base not found")
        active_revision = self.store.get_active_index_revision(tenant_id, kb_id)
        if revision == active_revision:
            raise IndexLifecycleError("shadow revision must differ from the active revision")
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        build_id = str(uuid.uuid4())
        started = time.perf_counter()
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"rag-index-build:{tenant_id}:{kb_id}:{revision}",),
            )
            cur.execute(
                """SELECT status FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                (tenant_id, kb_id, revision),
            )
            if cur.fetchone():
                raise IndexLifecycleError("index revision already exists and is immutable")
            cur.execute(
                """INSERT INTO rag_index_manifests
                (tenant_id,kb_id,index_revision,status,parser_id,chunker_id,dense_model_id,
                 sparse_model_id,reranker_id,config_json,collection_name,source_revision,build_id,
                 gate_status,quality_report,build_error,created_at,updated_at)
                VALUES (%s,%s,%s,'BUILDING',%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                        'PENDING','{}'::jsonb,'',NOW(),NOW())
                """,
                (
                    tenant_id, kb_id, revision, parser_id, chunker_id, dense_model_id,
                    sparse_model_id, reranker_id, json.dumps(effective_config, ensure_ascii=False),
                    collection_name, active_revision, build_id,
                ),
            )
        try:
            counts = self._snapshot_source(tenant_id, kb_id, revision)
            rows = self._snapshot_rows(tenant_id, kb_id, revision)
            if self.store.qdrant.collection_exists(collection_name=collection_name):
                self.store.qdrant.delete_collection(collection_name=collection_name)
            vector_size = 0
            for row in rows:
                vector = list(row.get("embedding_json") or [])
                if vector:
                    vector_size = len(vector)
                    break
            if vector_size <= 0:
                raise IndexQualityGateFailed("shadow index has no embedding vectors")
            self.store._ensure_collection(collection_name, vector_size)
            batch_size = max(8, min(int(effective_config.get("qdrant_batch_size", 16)), 256))
            published = 0
            for offset in range(0, len(rows), batch_size):
                points = []
                for row in rows[offset : offset + batch_size]:
                    vector = [float(value) for value in (row.get("embedding_json") or [])]
                    if len(vector) != vector_size:
                        raise IndexQualityGateFailed("shadow index contains incomplete or inconsistent embeddings")
                    metadata = dict(row.get("metadata") or {})
                    points.append(qmodels.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{revision}:{row['chunk_id']}")),
                        vector=vector,
                        payload={
                            "tenant_id": tenant_id,
                            "kb_id": kb_id,
                            "index_revision": revision,
                            "doc_id": row["doc_id"],
                            "chunk_id": row["chunk_id"],
                            "chunk_index": int(row.get("chunk_index") or 0),
                            "text": self.store._strip_nul(str(row.get("text") or "")),
                            "source": self.store._strip_nul(str(metadata.get("filename") or "unknown")),
                            "parent_id": str(row.get("parent_id") or ""),
                            "section_path": list(row.get("section_path") or []),
                            "block_type": str(row.get("block_type") or "paragraph"),
                            "page_start": int(row.get("page_start") or 0),
                            "page_end": int(row.get("page_end") or 0),
                        },
                    ))
                self.store.qdrant.upsert(collection_name=collection_name, points=points)
                published += len(points)
            report = self._integrity_report(counts, published)
            require_evaluation = bool(effective_config.get("require_evaluation", False))
            report["require_evaluation"] = require_evaluation
            gate_status = ("PENDING" if require_evaluation else "PASSED") if report["passed"] else "FAILED"
            status = "BUILDING" if report["passed"] else "FAILED"
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE rag_index_manifests SET status=%s,gate_status=%s,
                        quality_report=%s::jsonb,document_count=%s,chunk_count=%s,
                        lexical_chunk_count=%s,built_at=NOW(),updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                    (
                        status, gate_status, json.dumps(report, ensure_ascii=False),
                        int(counts.get("document_count") or 0), int(counts.get("chunk_count") or 0),
                        int(counts.get("lexical_chunk_count") or 0), tenant_id, kb_id, revision,
                    ),
                )
            if not report["passed"]:
                raise IndexQualityGateFailed("shadow index integrity gate failed")
            return {
                "tenant_id": tenant_id,
                "kb_id": kb_id,
                "index_revision": revision,
                "status": "BUILDING",
                "gate_status": gate_status,
                "collection_name": collection_name,
                "source_revision": active_revision,
                "build_id": build_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "quality_report": report,
            }
        except Exception as exc:
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE rag_index_manifests SET status='FAILED',gate_status='FAILED',
                        build_error=%s,built_at=COALESCE(built_at,NOW()),updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                    (str(exc)[:2000], tenant_id, kb_id, revision),
                )
            raise

    def evaluate(
        self,
        tenant_id: str,
        kb_id: str,
        revision: str,
        cases: list[dict],
        *,
        top_k: int = 10,
        min_recall: float = 0.0,
        min_mrr: float = 0.0,
        min_ndcg: float = 0.0,
    ) -> dict:
        revision = self._revision(revision)
        if not cases:
            raise ValueError("at least one evaluation case is required")
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT status FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                (tenant_id, kb_id, revision),
            )
            candidate = cur.fetchone()
        if not candidate:
            raise IndexLifecycleError("index manifest not found")
        if str(candidate.get("status") or "") != "BUILDING":
            raise IndexLifecycleError("only a building shadow revision can be evaluated")
        if not self.store.has_index_snapshot(tenant_id, kb_id, revision):
            # A required-evaluation build is intentionally PENDING and therefore
            # not visible through the normal snapshot resolver yet. Verify its
            # physical artifacts directly before allowing evaluation.
            collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
            if not self.store.qdrant.collection_exists(collection_name=collection_name):
                raise IndexLifecycleError("shadow index collection is missing")
        normalized_cases = [
            {**dict(case), "tenant_id": tenant_id, "kb_id": kb_id}
            for case in cases
        ]
        from retrieval.rag2_eval import evaluate_cases
        from retrieval.service import search_with_meta

        result = evaluate_cases(
            normalized_cases,
            top_k=max(1, min(int(top_k or 10), 50)),
            index_revision=revision,
            search_fn=lambda request: search_with_meta(request, store=self.store),
        )
        aggregate = dict(result.get("aggregate") or {})
        thresholds = {
            "recall_at_k": max(0.0, min(float(min_recall), 1.0)),
            "mrr_at_k": max(0.0, min(float(min_mrr), 1.0)),
            "ndcg_at_k": max(0.0, min(float(min_ndcg), 1.0)),
        }
        checks = {key: float(aggregate.get(key) or 0.0) >= threshold for key, threshold in thresholds.items()}
        passed = all(checks.values())
        evaluation = {**result, "thresholds": thresholds, "checks": checks, "passed": passed}
        with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """SELECT status,quality_report FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s FOR UPDATE""",
                (tenant_id, kb_id, revision),
            )
            manifest = cur.fetchone()
            if not manifest:
                raise IndexLifecycleError("index manifest not found")
            if str(manifest.get("status") or "") != "BUILDING":
                raise IndexLifecycleError("shadow revision changed state during evaluation")
            quality = dict(manifest.get("quality_report") or {})
            quality["evaluation"] = evaluation
            cur.execute(
                """UPDATE rag_index_manifests SET gate_status=%s,
                    status=CASE WHEN %s THEN status ELSE 'FAILED' END,
                    quality_report=%s::jsonb,
                    build_error=%s,updated_at=NOW()
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                (
                    "PASSED" if passed else "FAILED",
                    passed,
                    json.dumps(quality, ensure_ascii=False),
                    "" if passed else "retrieval evaluation gate failed",
                    tenant_id, kb_id, revision,
                ),
            )
        if not passed:
            raise IndexQualityGateFailed("retrieval evaluation gate failed")
        return evaluation

    def cleanup_failed(self, tenant_id: str, kb_id: str, revision: str) -> dict:
        revision = self._revision(revision)
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT status FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                (tenant_id, kb_id, revision),
            )
            manifest = cur.fetchone()
        if not manifest:
            raise IndexLifecycleError("index manifest not found")
        if str(manifest.get("status") or "") != "FAILED":
            raise IndexLifecycleError("only a failed index revision can be cleaned up")
        # Delete the remote artifact first. If it fails, the FAILED manifest is
        # intentionally retained so the same cleanup request remains retryable.
        if self.store.qdrant.collection_exists(collection_name=collection_name):
            self.store.qdrant.delete_collection(collection_name=collection_name)
        with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """SELECT status FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s FOR UPDATE""",
                (tenant_id, kb_id, revision),
            )
            current = cur.fetchone()
            if not current or str(current.get("status") or "") != "FAILED":
                raise IndexLifecycleError("failed index revision changed state during cleanup")
            cur.execute(
                "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (tenant_id, kb_id, revision),
            )
            cur.execute(
                "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (tenant_id, kb_id, revision),
            )
            cur.execute(
                "DELETE FROM rag_index_manifests WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (tenant_id, kb_id, revision),
            )
        return {"tenant_id": tenant_id, "kb_id": kb_id, "index_revision": revision, "deleted": True}

    def activate(self, tenant_id: str, kb_id: str, revision: str, actor_id: str) -> dict:
        revision = self._revision(revision)
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        if not self.store.qdrant.collection_exists(collection_name=collection_name):
            raise IndexLifecycleError("shadow Qdrant collection is missing")
        current_revision = self.store.get_active_index_revision(tenant_id, kb_id)
        if (
            current_revision != revision
            and not self.store.has_index_snapshot(tenant_id, kb_id, current_revision)
        ):
            # Never retire a legacy serving generation before it has a complete,
            # isolated rollback snapshot. This is intentionally completed before
            # the pointer transaction so activation still fails closed.
            self.bootstrap_active_snapshot(tenant_id, kb_id, actor_id)
        with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"rag-index:{tenant_id}:{kb_id}",))
            cur.execute(
                """SELECT active_index_revision FROM knowledge_bases
                WHERE tenant_id=%s AND kb_id=%s FOR UPDATE""",
                (tenant_id, kb_id),
            )
            kb = cur.fetchone()
            if not kb:
                raise IndexLifecycleError("knowledge base not found")
            previous = str(kb.get("active_index_revision") or "legacy-v1")
            cur.execute(
                """SELECT status,gate_status,quality_report FROM rag_index_manifests
                WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s FOR UPDATE""",
                (tenant_id, kb_id, revision),
            )
            target = cur.fetchone()
            if not target:
                raise IndexLifecycleError("index manifest not found")
            if str(target.get("gate_status") or "") not in {"PASSED", "WAIVED"}:
                raise IndexQualityGateFailed("index quality gate has not passed")
            if str(target.get("status") or "") not in {"BUILDING", "RETIRED", "ACTIVE"}:
                raise IndexLifecycleError("index revision is not activatable")
            if previous != revision:
                cur.execute(
                    """UPDATE rag_index_manifests SET status='RETIRED',updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND status='ACTIVE'""",
                    (tenant_id, kb_id),
                )
                cur.execute(
                    """UPDATE rag_index_manifests SET status='ACTIVE',activated_at=NOW(),
                        activated_by=%s,updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s""",
                    (str(actor_id or "system"), tenant_id, kb_id, revision),
                )
                cur.execute(
                    """UPDATE knowledge_bases SET active_index_revision=%s,updated_at=NOW()
                    WHERE tenant_id=%s AND kb_id=%s""",
                    (revision, tenant_id, kb_id),
                )
        return {
            "tenant_id": tenant_id,
            "kb_id": kb_id,
            "previous_revision": previous,
            "active_index_revision": revision,
            "changed": previous != revision,
        }

    def _recover_immediate_predecessor_snapshot(
        self, tenant_id: str, kb_id: str, revision: str, actor_id: str
    ) -> dict:
        """Repair a legacy predecessor only when no authoring data changed after cutover."""
        revision = self._revision(revision)
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        if (
            self.store.has_index_snapshot(tenant_id, kb_id, revision)
            and self.store.qdrant.collection_exists(collection_name=collection_name)
        ):
            return self.store.get_rag_index_manifest(tenant_id, kb_id, revision)
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT active.index_revision AS active_revision,
                          active.source_revision,active.activated_at,
                          target.status AS target_status,target.document_count,
                          target.chunk_count,target.lexical_chunk_count,
                          NOT EXISTS (
                              SELECT 1 FROM documents d
                              WHERE d.tenant_id=%s AND d.kb_id=%s
                                AND d.updated_at>active.activated_at
                          ) AS source_unchanged
                   FROM rag_index_manifests active
                   JOIN rag_index_manifests target
                     ON target.tenant_id=active.tenant_id AND target.kb_id=active.kb_id
                    AND target.index_revision=%s
                   WHERE active.tenant_id=%s AND active.kb_id=%s
                     AND active.status='ACTIVE' LIMIT 1""",
                (tenant_id, kb_id, revision, tenant_id, kb_id),
            )
            state = cur.fetchone() or {}
        if str(state.get("source_revision") or "") != revision:
            raise IndexLifecycleError("rollback target is not the immediate predecessor")
        if str(state.get("target_status") or "") != "RETIRED":
            raise IndexLifecycleError("rollback target is not retired")
        if not bool(state.get("source_unchanged")):
            raise IndexLifecycleError("rollback snapshot is missing and source documents changed after activation")
        with self.store._qdrant_collection_locks([collection_name]):
            counts = self._snapshot_source(tenant_id, kb_id, revision)
            try:
                expected = {
                    "document_count": int(state.get("document_count") or 0),
                    "chunk_count": int(state.get("chunk_count") or 0),
                    "lexical_chunk_count": int(state.get("lexical_chunk_count") or 0),
                }
                actual = {key: int(counts.get(key) or 0) for key in expected}
                if any(expected[key] and expected[key] != actual[key] for key in expected):
                    raise IndexLifecycleError("rollback source no longer matches the retired manifest")
                published = self._publish_snapshot_collection(
                    tenant_id, kb_id, revision, collection_name
                )
                report = self._integrity_report(counts, published)
                report.update({
                    "mode": "recovered-immediate-predecessor",
                    "snapshot_ready": bool(report["passed"]),
                })
                if not report["passed"]:
                    raise IndexQualityGateFailed("recovered rollback snapshot integrity gate failed")
                with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
                    cur.execute(
                        """UPDATE rag_index_manifests SET collection_name=%s,
                            document_count=%s,chunk_count=%s,lexical_chunk_count=%s,
                            gate_status='PASSED',quality_report=%s::jsonb,build_error='',
                            built_at=NOW(),activated_by=%s,updated_at=NOW()
                        WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s
                          AND status='RETIRED'""",
                        (
                            collection_name, actual["document_count"], actual["chunk_count"],
                            actual["lexical_chunk_count"], json.dumps(report, ensure_ascii=False),
                            str(actor_id or "system"), tenant_id, kb_id, revision,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise IndexLifecycleError("rollback target changed while its snapshot was recovering")
                return self.store.get_rag_index_manifest(tenant_id, kb_id, revision)
            except Exception:
                if self.store.qdrant.collection_exists(collection_name=collection_name):
                    self.store.qdrant.delete_collection(collection_name=collection_name)
                with self.store._conn() as conn, conn.transaction(), conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                        (tenant_id, kb_id, revision),
                    )
                    cur.execute(
                        "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                        (tenant_id, kb_id, revision),
                    )
                raise

    def rollback(self, tenant_id: str, kb_id: str, revision: str, actor_id: str) -> dict:
        collection_name = self.store._index_collection_name(tenant_id, kb_id, revision)
        if (
            not self.store.has_index_snapshot(tenant_id, kb_id, revision)
            or not self.store.qdrant.collection_exists(collection_name=collection_name)
        ):
            self._recover_immediate_predecessor_snapshot(tenant_id, kb_id, revision, actor_id)
        result = self.activate(tenant_id, kb_id, revision, actor_id)
        result["operation"] = "rollback"
        return result


index_lifecycle = RagIndexLifecycle()
