from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import urllib3
from minio import Minio
from minio.versioningconfig import ENABLED


SUFFIX_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def client_from_environment() -> Minio:
    endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
    access_key = os.environ.get("MINIO_ACCESS_KEY")
    secret_key = os.environ.get("MINIO_SECRET_KEY")
    if not access_key or not secret_key:
        raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")
    secure = os.environ.get("MINIO_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
    ca_cert_path = os.environ.get("MINIO_CA_CERT_PATH", "").strip()
    http_client = None
    if secure:
        if not ca_cert_path:
            raise RuntimeError("MINIO_CA_CERT_PATH is required when MINIO_SECURE is enabled")
        if not Path(ca_cert_path).is_file():
            raise RuntimeError("MINIO_CA_CERT_PATH does not reference a readable file")
        http_client = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=ca_cert_path)
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        http_client=http_client,
    )


def drill_scope(suffix: str) -> tuple[str, str, str]:
    if not SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError("suffix must be exactly 32 lowercase hexadecimal characters")
    bucket = os.environ.get("MINIO_DOCUMENT_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("MINIO_DOCUMENT_BUCKET is required")
    root = f"v1/system/backup-restore-drills/{suffix}"
    return bucket, f"{root}/source", f"{root}/restore"


def remove_prefix_versions(client: Minio, bucket: str, prefix: str) -> None:
    # Deleting only current objects in a versioned bucket would create markers.
    for item in client.list_objects(bucket, prefix=prefix, recursive=True, include_version=True):
        client.remove_object(bucket, item.object_name, version_id=item.version_id)
    remaining = list(client.list_objects(bucket, prefix=prefix, recursive=True, include_version=True))
    if remaining:
        raise RuntimeError(f"versioned prefix cleanup left {len(remaining)} entries")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def download_object(client: Minio, bucket: str, object_name: str, version_id: str | None = None) -> bytes:
    response = client.get_object(bucket, object_name, version_id=version_id)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def safe_target(root: Path, object_name: str) -> Path:
    relative = PurePosixPath(object_name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RuntimeError(f"unsafe object name in backup: {object_name}")
    target = root.joinpath(*relative.parts).resolve()
    root_prefix = str(root.resolve()) + os.sep
    if not str(target).startswith(root_prefix):
        raise RuntimeError(f"object path escaped backup root: {object_name}")
    return target


def extract_archive_safely(archive: Path, output_root: Path) -> None:
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                continue
            target = safe_target(output_root, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read archive member: {member.name}")
            with source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def run_drill(client: Minio, suffix: str, archive_path: Path) -> dict[str, object]:
    bucket, source_prefix, restore_prefix = drill_scope(suffix)
    canary_versions = {
        "documents/alpha.txt": [
            b"agent-powflow-minio-drill-alpha-v1\n",
            b"agent-powflow-minio-drill-alpha-v2\n",
        ],
        "documents/nested/beta.json": [
            b'{"marker":"beta","ordinal":1}\n',
            b'{"marker":"beta","ordinal":2}\n',
        ],
        "binary/blob.bin": [bytes(range(256)) * 4],
    }
    workspace = Path(tempfile.mkdtemp(prefix=f"apf-minio-{suffix}-"))
    backup_root = workspace / "backup"
    restore_root = workspace / "restore"
    backup_root.mkdir(parents=True)
    restore_root.mkdir(parents=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        versioning = client.get_bucket_versioning(bucket)
        if versioning.status != ENABLED:
            raise RuntimeError(f"document bucket versioning is not enabled: status={versioning.status!r}")
        backup_manifest: dict[str, object] = {"format_version": 1, "objects": []}
        for name, values in canary_versions.items():
            source_name = f"{source_prefix}/{name}"
            versions: list[dict[str, object]] = []
            for ordinal, value in enumerate(values, start=1):
                result = client.put_object(
                    bucket,
                    source_name,
                    io.BytesIO(value),
                    len(value),
                    content_type="application/octet-stream",
                )
                if not result.version_id:
                    raise RuntimeError(f"versioning did not return a version id: {name}#{ordinal}")
                actual = download_object(client, bucket, source_name, result.version_id)
                if actual != value:
                    raise RuntimeError(f"source version mismatch: {name}#{ordinal}")
                relative_path = f"payloads/{name}/{ordinal:04d}.bin"
                target = safe_target(backup_root, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(actual)
                versions.append(
                    {
                        "ordinal": ordinal,
                        "bytes": len(value),
                        "sha256": sha256_bytes(value),
                        "archive_path": relative_path,
                    }
                )
            backup_manifest["objects"].append({"name": name, "versions": versions})

        source_names = sorted(
            item.object_name.removeprefix(f"{source_prefix}/")
            for item in client.list_objects(bucket, prefix=source_prefix, recursive=True)
        )
        if source_names != sorted(canary_versions):
            raise RuntimeError(f"source object inventory mismatch: {source_names}")

        source_version_count = len(
            list(client.list_objects(bucket, prefix=source_prefix, recursive=True, include_version=True))
        )
        expected_version_count = sum(len(values) for values in canary_versions.values())
        if source_version_count != expected_version_count:
            raise RuntimeError(
                f"source version inventory mismatch: expected={expected_version_count} actual={source_version_count}"
            )

        manifest_file = safe_target(backup_root, "manifest.json")
        manifest_file.write_text(json.dumps(backup_manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")

        with tarfile.open(archive_path, mode="w:gz") as bundle:
            for path in sorted(item for item in backup_root.rglob("*") if item.is_file()):
                bundle.add(path, arcname=path.relative_to(backup_root).as_posix(), recursive=False)
        if archive_path.stat().st_size < 1:
            raise RuntimeError("generated MinIO canary archive is empty")

        extract_archive_safely(archive_path, restore_root)
        restored_manifest = json.loads(safe_target(restore_root, "manifest.json").read_text(encoding="utf-8"))
        if restored_manifest != backup_manifest:
            raise RuntimeError("archive manifest changed during extraction")
        restored_version_ids: dict[str, list[str]] = {}
        for obj in restored_manifest["objects"]:
            name = str(obj["name"])
            restore_name = f"{restore_prefix}/{name}"
            restored_version_ids[name] = []
            for version in obj["versions"]:
                value = safe_target(restore_root, str(version["archive_path"])).read_bytes()
                if len(value) != int(version["bytes"]) or sha256_bytes(value) != str(version["sha256"]):
                    raise RuntimeError(f"archive payload mismatch: {name}#{version['ordinal']}")
                result = client.put_object(
                    bucket,
                    restore_name,
                    io.BytesIO(value),
                    len(value),
                    content_type="application/octet-stream",
                )
                if not result.version_id:
                    raise RuntimeError(f"restore did not create a version id: {name}#{version['ordinal']}")
                restored_version_ids[name].append(result.version_id)

        restored_names = sorted(
            item.object_name.removeprefix(f"{restore_prefix}/")
            for item in client.list_objects(bucket, prefix=restore_prefix, recursive=True)
        )
        if restored_names != source_names:
            raise RuntimeError(f"restored object inventory mismatch: {restored_names}")
        restored_version_count = len(
            list(client.list_objects(bucket, prefix=restore_prefix, recursive=True, include_version=True))
        )
        if restored_version_count != expected_version_count:
            raise RuntimeError(
                f"restored version inventory mismatch: expected={expected_version_count} actual={restored_version_count}"
            )
        for obj in restored_manifest["objects"]:
            name = str(obj["name"])
            restore_name = f"{restore_prefix}/{name}"
            for version, version_id in zip(obj["versions"], restored_version_ids[name], strict=True):
                value = download_object(client, bucket, restore_name, version_id)
                if len(value) != int(version["bytes"]) or sha256_bytes(value) != str(version["sha256"]):
                    raise RuntimeError(f"restored object version mismatch: {name}#{version['ordinal']}")

        return {
            "object_count": len(restored_names),
            "version_count": expected_version_count,
            "payload_bytes": sum(len(value) for values in canary_versions.values() for value in values),
            "objects": backup_manifest["objects"],
            "document_bucket": bucket,
            "bucket_versioning_verified": True,
            "restored_objects_verified": True,
        }
    finally:
        cleanup_errors: list[str] = []
        for prefix in (restore_prefix, source_prefix):
            try:
                remove_prefix_versions(client, bucket, prefix)
            except Exception as exc:  # reported after attempting both buckets
                cleanup_errors.append(f"{prefix}: {exc}")
        shutil.rmtree(workspace, ignore_errors=True)
        if cleanup_errors:
            raise RuntimeError("MinIO canary cleanup failed: " + "; ".join(cleanup_errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--archive")
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    bucket, source_prefix, restore_prefix = drill_scope(args.suffix)
    client = client_from_environment()

    if args.cleanup_only:
        errors: list[str] = []
        for prefix in (restore_prefix, source_prefix):
            try:
                remove_prefix_versions(client, bucket, prefix)
            except Exception as exc:
                errors.append(f"{prefix}: {exc}")
        if errors:
            raise RuntimeError("MinIO cleanup-only failed: " + "; ".join(errors))
        print(json.dumps({"cleanup": True}, separators=(",", ":")))
        return

    if not args.archive:
        raise ValueError("--archive is required unless --cleanup-only is used")
    result = run_drill(client, args.suffix, Path(args.archive))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
