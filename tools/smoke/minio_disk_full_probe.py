from __future__ import annotations

import argparse
import hashlib
import io
import json
import time

from minio import Minio
from minio.error import S3Error


def wait_ready(client: Minio, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.list_buckets()
            return
        except Exception as exc:  # service is still starting
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"isolated MinIO did not become ready: {type(last_error).__name__}")


def remove_bucket(client: Minio, bucket: str) -> None:
    try:
        for item in client.list_objects(bucket, recursive=True, include_version=True):
            client.remove_object(bucket, item.object_name, version_id=item.version_id)
        remaining = list(client.list_objects(bucket, recursive=True, include_version=True))
        if remaining:
            raise RuntimeError(f"cleanup left {len(remaining)} object entries")
        client.remove_bucket(bucket)
    except S3Error as exc:
        if exc.code not in {"NoSuchBucket", "NoSuchKey"}:
            raise


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    client = Minio(
        args.endpoint,
        access_key=args.access_key,
        secret_key=args.secret_key,
        secure=False,
    )
    wait_ready(client)
    client.make_bucket(args.bucket)
    payload = bytes((index % 251 for index in range(args.object_mib * 1024 * 1024)))
    successful_objects: list[str] = []
    storage_error: S3Error | None = None

    try:
        for ordinal in range(1, args.max_objects + 1):
            name = f"fill/{ordinal:04d}.bin"
            try:
                client.put_object(
                    args.bucket,
                    name,
                    io.BytesIO(payload),
                    len(payload),
                    content_type="application/octet-stream",
                )
                successful_objects.append(name)
            except S3Error as exc:
                storage_error = exc
                break

        if not successful_objects:
            raise RuntimeError("isolated capacity was too small to establish a successful-write baseline")
        if storage_error is None:
            raise RuntimeError("bounded probe reached max_objects without producing a storage-full response")
        if storage_error.code != "XMinioStorageFull":
            raise RuntimeError(f"unexpected MinIO capacity error code: {storage_error.code}")

        for name in successful_objects:
            client.remove_object(args.bucket, name)
        if list(client.list_objects(args.bucket, recursive=True, include_version=True)):
            raise RuntimeError("fill objects remain after recovery cleanup")

        recovery_payload = b"agent-powflow-minio-disk-full-recovered\n" * 1024
        recovery_name = "recovery/probe.bin"
        client.put_object(
            args.bucket,
            recovery_name,
            io.BytesIO(recovery_payload),
            len(recovery_payload),
            content_type="application/octet-stream",
        )
        response = client.get_object(args.bucket, recovery_name)
        try:
            restored = response.read()
        finally:
            response.close()
            response.release_conn()
        if hashlib.sha256(restored).digest() != hashlib.sha256(recovery_payload).digest():
            raise RuntimeError("recovery object checksum mismatch")

        return {
            "storage_full_observed": True,
            "storage_error_code": storage_error.code,
            "successful_object_count": len(successful_objects),
            "successful_payload_bytes": len(successful_objects) * len(payload),
            "recovery_write_verified": True,
            "recovery_payload_bytes": len(recovery_payload),
        }
    finally:
        remove_bucket(client, args.bucket)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--access-key", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--object-mib", type=int, default=4)
    parser.add_argument("--max-objects", type=int, default=64)
    args = parser.parse_args()
    if not 1 <= args.object_mib <= 8:
        raise ValueError("object-mib must be between 1 and 8")
    if not 2 <= args.max_objects <= 128:
        raise ValueError("max-objects must be between 2 and 128")
    result = run_probe(args)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
