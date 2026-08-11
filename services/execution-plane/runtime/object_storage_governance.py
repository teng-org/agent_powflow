from __future__ import annotations

from dataclasses import dataclass
import os
import time

from minio.commonconfig import ENABLED, Filter
from minio.error import S3Error
from minio.lifecycleconfig import (
    Expiration,
    LifecycleConfig,
    NoncurrentVersionExpiration,
    Rule,
)
from minio.versioningconfig import VersioningConfig

from runtime.object_storage import (
    DOCUMENT_BUCKET_MANAGED_RULE_ID,
    ObjectStorage,
    ObjectStorageError,
)


MANAGED_RULE_ID = DOCUMENT_BUCKET_MANAGED_RULE_ID


@dataclass(frozen=True)
class DocumentBucketPolicy:
    noncurrent_expiry_days: int
    retain_noncurrent_versions: int

    @classmethod
    def from_env(cls) -> "DocumentBucketPolicy":
        return cls(
            noncurrent_expiry_days=_bounded_env_int(
                "MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS", 90, 1, 3650
            ),
            retain_noncurrent_versions=_bounded_env_int(
                "MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS", 2, 1, 100
            ),
        )


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ObjectStorageError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ObjectStorageError(f"{name} must be between {minimum} and {maximum}")
    return value


def _rule_id(rule: Rule) -> str:
    # minio-py serializes the ID but currently exposes no public ID accessor.
    return str(getattr(rule, "_rule_id", "") or "")


def _get_lifecycle_rules(storage: ObjectStorage) -> list[Rule]:
    try:
        lifecycle = storage.client.get_bucket_lifecycle(storage.bucket)
    except S3Error as exc:
        if exc.code in {"NoSuchLifecycleConfiguration", "NoSuchConfiguration"}:
            return []
        raise
    return list(getattr(lifecycle, "rules", None) or []) if lifecycle is not None else []


def _managed_rule(policy: DocumentBucketPolicy) -> Rule:
    return Rule(
        ENABLED,
        expiration=Expiration(expired_object_delete_marker=True),
        rule_filter=Filter(prefix="v1/"),
        rule_id=MANAGED_RULE_ID,
        noncurrent_version_expiration=NoncurrentVersionExpiration(
            noncurrent_days=policy.noncurrent_expiry_days,
            newer_noncurrent_versions=policy.retain_noncurrent_versions,
        ),
    )


def configure_document_bucket(
    storage: ObjectStorage | None = None,
    policy: DocumentBucketPolicy | None = None,
) -> dict:
    target = storage or ObjectStorage()
    desired = policy or DocumentBucketPolicy.from_env()
    target.ensure_bucket()
    target.client.set_bucket_versioning(target.bucket, VersioningConfig(ENABLED))

    existing = _get_lifecycle_rules(target)
    preserved = [rule for rule in existing if _rule_id(rule) != MANAGED_RULE_ID]
    target.client.set_bucket_lifecycle(
        target.bucket,
        LifecycleConfig(preserved + [_managed_rule(desired)]),
    )

    versioning = target.client.get_bucket_versioning(target.bucket)
    versioning_status = str(getattr(versioning, "status", None) or "DISABLED").upper()
    rules = _get_lifecycle_rules(target)
    managed_rules = [rule for rule in rules if _rule_id(rule) == MANAGED_RULE_ID]
    managed_count = len(managed_rules)
    noncurrent = (
        getattr(managed_rules[0], "noncurrent_version_expiration", None)
        if managed_count == 1
        else None
    )
    applied_expiry_days = int(getattr(noncurrent, "_noncurrent_days", 0) or 0)
    applied_retain_versions = int(getattr(noncurrent, "_newer_noncurrent_versions", 0) or 0)
    if (
        versioning_status != "ENABLED"
        or managed_count != 1
        or applied_expiry_days != desired.noncurrent_expiry_days
        or applied_retain_versions != desired.retain_noncurrent_versions
    ):
        raise ObjectStorageError("document bucket governance verification failed")
    return {
        "bucket": target.bucket,
        "versioning_status": versioning_status,
        "lifecycle_rule_count": len(rules),
        "managed_rule_count": managed_count,
        "noncurrent_expiry_days": desired.noncurrent_expiry_days,
        "retain_noncurrent_versions": desired.retain_noncurrent_versions,
    }


def main() -> None:
    attempts = _bounded_env_int("MINIO_GOVERNANCE_INIT_ATTEMPTS", 30, 1, 120)
    interval = _bounded_env_int("MINIO_GOVERNANCE_INIT_INTERVAL_SECONDS", 2, 1, 30)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = configure_document_bucket()
            print(
                "MINIO_DOCUMENT_GOVERNANCE_OK "
                f"bucket={result['bucket']} versioning={result['versioning_status']} "
                f"rules={result['lifecycle_rule_count']} managed_rules={result['managed_rule_count']}"
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(interval)
    raise SystemExit(f"MINIO_DOCUMENT_GOVERNANCE_FAILED reason={type(last_error).__name__}")


if __name__ == "__main__":
    main()
