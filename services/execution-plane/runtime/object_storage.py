from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
import re
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error
import urllib3


class ObjectStorageError(RuntimeError):
    pass


DOCUMENT_BUCKET_MANAGED_RULE_ID = "agent-powflow-document-retention-v1"
MAX_OBJECT_FILENAME_SEGMENT_LENGTH = 200


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str


class ObjectStorage:
    def __init__(self, client=None, bucket: str | None = None) -> None:
        self.bucket = (bucket or os.getenv("MINIO_DOCUMENT_BUCKET", "agent-powflow-documents")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise ObjectStorageError("MINIO_DOCUMENT_BUCKET is invalid")
        self.secure = os.getenv("MINIO_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        if client is not None:
            self.client = client
        else:
            ca_path = str(os.getenv("MINIO_CA_CERT_PATH", "") or "").strip()
            http_client = None
            if self.secure and ca_path:
                if not os.path.isfile(ca_path):
                    raise ObjectStorageError("MINIO_CA_CERT_PATH is not a readable file")
                http_client = urllib3.PoolManager(
                    cert_reqs="CERT_REQUIRED",
                    ca_certs=ca_path,
                    retries=False,
                )
            self.client = Minio(
                endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000").strip(),
                access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
                secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
                secure=self.secure,
                http_client=http_client,
            )

    @staticmethod
    def _scope_hash(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = os.path.basename(str(filename or "source.bin").replace("\\", "/")) or "source.bin"
        encoded = quote(name, safe="._-")
        if len(encoded) <= MAX_OBJECT_FILENAME_SEGMENT_LENGTH:
            return encoded

        stem, suffix = os.path.splitext(name)
        suffix_encoded = quote(suffix[-16:], safe="._-")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        budget = MAX_OBJECT_FILENAME_SEGMENT_LENGTH - len(suffix_encoded) - len(digest) - 1
        readable_parts: list[str] = []
        used = 0
        for character in stem:
            token = quote(character, safe="._-")
            if used + len(token) > budget:
                break
            readable_parts.append(token)
            used += len(token)
        readable = "".join(readable_parts).rstrip("._-") or "source"
        return f"{readable}-{digest}{suffix_encoded}"

    def document_keys(self, tenant_id: str, kb_id: str, doc_id: str, filename: str) -> tuple[str, str]:
        prefix = (
            f"v1/tenants/{self._scope_hash(tenant_id)}/kbs/{self._scope_hash(kb_id)}/"
            f"documents/{quote(str(doc_id), safe='-_')}"
        )
        return f"{prefix}/source/{self._safe_filename(filename)}", f"{prefix}/extracted/text.txt"

    def ensure_bucket(self) -> None:
        try:
            if self.client.bucket_exists(self.bucket):
                return
            self.client.make_bucket(self.bucket)
        except S3Error as exc:
            if exc.code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise ObjectStorageError(f"object bucket unavailable: {exc.code}") from exc
        except Exception as exc:
            raise ObjectStorageError("object bucket unavailable") from exc

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        raw = bytes(data or b"")
        if not raw:
            raise ObjectStorageError("refusing to store an empty object")
        safe_key = str(key or "").strip().lstrip("/")
        if not safe_key or ".." in safe_key.split("/"):
            raise ObjectStorageError("object key is invalid")
        digest = hashlib.sha256(raw).hexdigest()
        self.ensure_bucket()
        try:
            self.client.put_object(
                self.bucket,
                safe_key,
                io.BytesIO(raw),
                len(raw),
                content_type=content_type or "application/octet-stream",
                metadata={**(metadata or {}), "sha256": digest},
            )
        except Exception as exc:
            raise ObjectStorageError("object upload failed") from exc
        return StoredObject(self.bucket, safe_key, digest, len(raw), content_type or "application/octet-stream")

    def get_bytes(self, bucket: str, key: str, expected_sha256: str = "") -> bytes:
        if bucket != self.bucket:
            raise ObjectStorageError("object bucket does not match configured document bucket")
        response = None
        try:
            response = self.client.get_object(bucket, key)
            limit = max(1, int(os.getenv("OBJECT_STORAGE_MAX_READ_BYTES", str(64 * 1024 * 1024))))
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(min(1024 * 1024, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ObjectStorageError("object exceeds configured read limit")
                chunks.append(chunk)
            raw = b"".join(chunks)
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise ObjectStorageError("object download failed") from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise ObjectStorageError("object checksum mismatch")
        return raw

    def delete_object(self, bucket: str, key: str) -> None:
        if bucket != self.bucket:
            raise ObjectStorageError("object bucket does not match configured document bucket")
        try:
            versions = [
                item for item in self.client.list_objects(
                    bucket, prefix=key, recursive=True, include_version=True
                )
                if item.object_name == key
            ]
            for item in versions:
                self.client.remove_object(bucket, key, version_id=item.version_id)
        except Exception as exc:
            raise ObjectStorageError("object deletion failed") from exc

    def governance_status(self) -> dict:
        """Inspect bucket durability controls without mutating them."""
        try:
            exists = bool(self.client.bucket_exists(self.bucket))
            if not exists:
                return {
                    "health": "CRITICAL",
                    "bucket": self.bucket,
                    "bucket_exists": False,
                    "secure_transport": self.secure,
                    "versioning_status": "UNAVAILABLE",
                    "lifecycle_configured": False,
                    "lifecycle_rule_count": 0,
                    "reasons": ["document bucket does not exist"],
                }
            versioning = self.client.get_bucket_versioning(self.bucket)
            versioning_status = str(getattr(versioning, "status", None) or "DISABLED").upper()
            lifecycle = self.client.get_bucket_lifecycle(self.bucket)
            rules = list(getattr(lifecycle, "rules", None) or []) if lifecycle is not None else []
            managed_rule_count = sum(
                1
                for rule in rules
                if str(getattr(rule, "_rule_id", "") or "") == DOCUMENT_BUCKET_MANAGED_RULE_ID
            )
            managed_rule = next(
                (
                    rule
                    for rule in rules
                    if str(getattr(rule, "_rule_id", "") or "") == DOCUMENT_BUCKET_MANAGED_RULE_ID
                ),
                None,
            )
            noncurrent = (
                getattr(managed_rule, "noncurrent_version_expiration", None)
                if managed_rule is not None
                else None
            )
            applied_expiry_days = int(getattr(noncurrent, "_noncurrent_days", 0) or 0)
            applied_retain_versions = int(getattr(noncurrent, "_newer_noncurrent_versions", 0) or 0)
            expected_expiry_days = int(os.getenv("MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS", "90"))
            expected_retain_versions = int(os.getenv("MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS", "2"))
            reasons = []
            if versioning_status != "ENABLED":
                reasons.append("bucket versioning is not enabled")
            if not rules:
                reasons.append("bucket lifecycle rules are not configured")
            elif managed_rule_count != 1:
                reasons.append("Agent Powflow managed lifecycle rule is not configured")
            elif (
                applied_expiry_days != expected_expiry_days
                or applied_retain_versions != expected_retain_versions
            ):
                reasons.append("Agent Powflow managed lifecycle rule differs from configured retention")
            if not self.secure:
                reasons.append("MinIO transport is not using TLS")
            return {
                "health": "WARN" if reasons else "OK",
                "bucket": self.bucket,
                "bucket_exists": True,
                "secure_transport": self.secure,
                "versioning_status": versioning_status,
                "lifecycle_configured": bool(rules),
                "lifecycle_rule_count": len(rules),
                "managed_lifecycle_rule_id": DOCUMENT_BUCKET_MANAGED_RULE_ID,
                "managed_lifecycle_rule_count": managed_rule_count,
                "noncurrent_expiry_days": applied_expiry_days or None,
                "retain_noncurrent_versions": applied_retain_versions or None,
                "expected_noncurrent_expiry_days": expected_expiry_days,
                "expected_retain_noncurrent_versions": expected_retain_versions,
                "reasons": reasons,
            }
        except Exception as exc:
            raise ObjectStorageError("object storage governance inspection failed") from exc


object_storage = ObjectStorage()
