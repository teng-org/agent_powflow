from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from pathlib import Path
from unittest.mock import Mock, call, patch

import httpx


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from api.app import main as api_main
from postgres_test_support import IsolatedPostgresCase
from runtime.object_storage import ObjectStorage
from test_object_storage import InMemoryMinio


class ExecutionApiIntegrationTest(IsolatedPostgresCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.legacy_write_patch = patch.dict(
            api_main.os.environ, {"EXECUTION_LEGACY_KB_WRITE_ENABLED": "true"}
        )
        cls.legacy_write_patch.start()
        cls.original_store = api_main.kb_store
        api_main.kb_store = cls.store
        cls.audit_patch = patch.object(api_main.audit_sink, "emit", return_value=None)
        cls.revoke_patch = patch.object(api_main.celery_app.control, "revoke", return_value=None)
        cls.audit_patch.start()
        cls.revoke_mock = cls.revoke_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.revoke_patch.stop()
            cls.audit_patch.stop()
            cls.legacy_write_patch.stop()
            api_main.kb_store = cls.original_store
        finally:
            super().tearDownClass()

    def _headers(self, tenant_id: str) -> dict[str, str]:
        now = int(time.time())
        token = api_main._sign_token({"sub": "integration-admin", "role": "PLATFORM_ADMIN", "iat": now, "exp": now + 300})
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": tenant_id,
            "X-Actor-Id": "integration-admin",
            "X-Trace-Id": f"trace-{tenant_id}",
        }

    def _request(self, method: str, path: str, tenant_id: str, **kwargs) -> httpx.Response:
        extra_headers = dict(kwargs.pop("headers", {}) or {})
        headers = {**self._headers(tenant_id), **extra_headers}
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=api_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://execution.test") as client:
                return await client.request(method, path, headers=headers, **kwargs)
        return asyncio.run(send())

    def test_legacy_public_kb_write_is_gone_by_default(self):
        with patch.dict(api_main.os.environ, {"EXECUTION_LEGACY_KB_WRITE_ENABLED": "false"}):
            response = self._request("POST", "/api/v1/admin/kbs", "tenant-retired", json={"name": "retired"})
        self.assertEqual(410, response.status_code)
        self.assertEqual("LEGACY_KB_WRITE_RETIRED", response.json()["detail"]["code"])

    def test_kb_admin_routes_do_not_cross_tenant_boundaries(self):
        created = self._request("POST", "/api/v1/admin/kbs", "tenant-api-a", json={"name": "Private API KB"})
        self.assertEqual(200, created.status_code, created.text)
        kb_id = created.json()["item"]["kb_id"]

        own_list = self._request("GET", "/api/v1/admin/kbs", "tenant-api-a")
        other_list = self._request("GET", "/api/v1/admin/kbs", "tenant-api-b")
        self.assertEqual(1, len([item for item in own_list.json()["items"] if item["kb_id"] == kb_id]))
        self.assertEqual(0, len([item for item in other_list.json()["items"] if item["kb_id"] == kb_id]))

        other_rename = self._request("PATCH", f"/api/v1/admin/kbs/{kb_id}", "tenant-api-b", json={"name": "Hijacked"})
        other_delete = self._request("DELETE", f"/api/v1/admin/kbs/{kb_id}", "tenant-api-b")
        self.assertEqual(404, other_rename.status_code)
        self.assertEqual(404, other_delete.status_code)
        self.assertEqual("Private API KB", self.store.get_kb("tenant-api-a", kb_id)["name"])

    def test_internal_kb_delete_requires_control_token_and_preserves_tenant_scope(self):
        self.store.create_kb("tenant-internal-a", "kb-internal", "Internal cleanup")

        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "integration-internal-token"):
            denied = self._request(
                "DELETE",
                "/api/v1/internal/kbs/tenant-internal-a/kb-internal",
                "tenant-internal-a",
                headers={"X-Internal-Token": "wrong-token"},
            )
            missing = self._request(
                "DELETE",
                "/api/v1/internal/kbs/tenant-internal-b/kb-internal",
                "tenant-internal-b",
                headers={"X-Internal-Token": "integration-internal-token"},
            )
            deleted = self._request(
                "DELETE",
                "/api/v1/internal/kbs/tenant-internal-a/kb-internal",
                "tenant-internal-a",
                headers={"X-Internal-Token": "integration-internal-token"},
            )

        self.assertEqual(401, denied.status_code)
        self.assertEqual(404, missing.status_code)
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertIsNone(self.store.get_kb("tenant-internal-a", "kb-internal"))

    def test_internal_task_revoke_requires_control_token_and_bounds_batch(self):
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "integration-internal-token"):
            denied = self._request(
                "POST",
                "/api/v1/internal/tasks/revoke",
                "tenant-internal-a",
                headers={"X-Internal-Token": "wrong-token"},
                json={"task_ids": ["task-a"]},
            )
            empty = self._request(
                "POST",
                "/api/v1/internal/tasks/revoke",
                "tenant-internal-a",
                headers={"X-Internal-Token": "integration-internal-token"},
                json={"task_ids": ["", " "]},
            )
            self.revoke_mock.reset_mock()
            accepted = self._request(
                "POST",
                "/api/v1/internal/tasks/revoke",
                "tenant-internal-a",
                headers={"X-Internal-Token": "integration-internal-token"},
                json={"task_ids": ["task-a", "task-a", "task-b"]},
            )

        self.assertEqual(401, denied.status_code)
        self.assertEqual(400, empty.status_code)
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertEqual(["task-a", "task-b"], accepted.json()["revoked_task_ids"])
        self.assertEqual([("task-a",), ("task-b",)], [call.args for call in self.revoke_mock.call_args_list])

    def test_task_status_events_and_cancel_are_tenant_scoped(self):
        self.store.create_task_record("api-task", "tenant-api-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING")
        self.store.update_task_record("api-task", "RUNNING", "PARSING")

        hidden = self._request("GET", "/api/v1/tasks/api-task", "tenant-api-b")
        hidden_events = self._request("GET", "/api/v1/admin/tasks/api-task/events", "tenant-api-b")
        hidden_cancel = self._request("POST", "/api/v1/admin/tasks/api-task/cancel", "tenant-api-b")
        self.assertEqual("UNKNOWN", hidden.json()["state"])
        self.assertNotIn("record", hidden.json())
        self.assertEqual(404, hidden_events.status_code)
        self.assertEqual(404, hidden_cancel.status_code)
        self.assertEqual("RUNNING", self.store.get_task("api-task", tenant_id="tenant-api-a")["status"])

        visible = self._request("GET", "/api/v1/tasks/api-task", "tenant-api-a")
        cancelled = self._request("POST", "/api/v1/admin/tasks/api-task/cancel", "tenant-api-a")
        self.assertEqual("RUNNING", visible.json()["state"])
        self.assertEqual(200, cancelled.status_code, cancelled.text)
        self.assertEqual("CANCELLED", self.store.get_task("api-task", tenant_id="tenant-api-a")["status"])

    def test_cross_store_repair_ledger_is_visible_and_executable_to_platform_admin(self):
        repair_id = self.store.enqueue_cross_store_repair(
            store_name="UNSUPPORTED",
            operation="TEST_OPERATION",
            tenant_id="tenant-api-repair",
            kb_id="kb-repair",
            doc_id="doc-repair",
            error_message="seeded failure",
        )

        listed = self._request(
            "GET",
            "/api/v1/admin/system/cross-store-repairs?status=PENDING&limit=10",
            "tenant-api-repair",
        )
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual([repair_id], [item["repair_id"] for item in listed.json()["items"]])

        executed = self._request(
            "POST",
            "/api/v1/admin/system/cross-store-repairs/execute?limit=10",
            "tenant-api-repair",
        )
        self.assertEqual(200, executed.status_code, executed.text)
        self.assertEqual(1, executed.json()["item"]["failed"])
        failed = self.store.list_cross_store_repairs(status="FAILED")
        self.assertEqual("UNSUPPORTED/TEST_OPERATION", failed[-1]["last_error"].split(": ", 1)[-1])

        retried = self._request(
            "POST",
            f"/api/v1/admin/system/cross-store-repairs/{repair_id}/execute",
            "tenant-api-repair",
        )
        self.assertEqual(200, retried.status_code, retried.text)
        self.assertFalse(retried.json()["item"]["succeeded"])
        self.assertEqual("FAILED", retried.json()["item"]["record"]["status"])
        self.assertEqual(2, retried.json()["item"]["record"]["attempts"])
        missing = self._request(
            "POST",
            "/api/v1/admin/system/cross-store-repairs/999999999/execute",
            "tenant-api-repair",
        )
        self.assertEqual(404, missing.status_code)

        stats = self._request("GET", "/api/v1/admin/system/cross-store-repairs/stats", "tenant-api-repair")
        self.assertEqual(200, stats.status_code, stats.text)
        self.assertEqual("CRITICAL", stats.json()["item"]["health"])
        self.assertEqual(1, stats.json()["item"]["status_counts"]["FAILED"])
        self.assertTrue(stats.json()["item"]["health_reasons"])

        metrics = self._request("GET", "/api/v1/admin/system/cross-store-repairs/metrics", "tenant-api-repair")
        self.assertEqual(200, metrics.status_code, metrics.text)
        self.assertIn("text/plain", metrics.headers["content-type"])
        self.assertIn('agent_powflow_cross_store_repairs{status="failed"} 1', metrics.text)
        self.assertIn('agent_powflow_cross_store_repair_health{state="critical"} 1', metrics.text)

        alerts = self._request("GET", "/api/v1/admin/system/alerts/preview", "tenant-api-repair")
        self.assertEqual(200, alerts.status_code, alerts.text)
        repair_alerts = [item for item in alerts.json()["items"] if item["alert_type"] == "cross_store_repair"]
        self.assertEqual(1, len(repair_alerts))
        self.assertEqual("critical", repair_alerts[0]["severity"])
        self.assertEqual("platform/cross-store", repair_alerts[0]["scope"])

    def test_multipart_upload_persists_source_and_extracted_objects_and_dispatches_reference(self):
        tenant_id = "tenant-api-object"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Object KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with patch.object(api_main, "object_storage", storage), patch.object(api_main, "_send_persistent_task") as dispatch:
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("evidence.txt", b"alpha beta", "text/plain")},
                data={"process_mode": "UPLOAD_CLEAN_CHUNK_EMBED", "chunk_method": "sentence"},
            )
            item = response.json()["accepted"][0]
            source = self._request(
                "GET",
                f"/api/v1/admin/kbs/{kb_id}/documents/{item['doc_id']}/source",
                tenant_id,
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(200, source.status_code, source.text)
        self.assertEqual(b"alpha beta", source.content)
        self.assertEqual(hashlib.sha256(b"alpha beta").hexdigest(), source.headers["x-content-sha256"])
        document = self.store.get_document(tenant_id, kb_id, item["doc_id"])
        self.assertEqual("UPLOADED_RAW", document["status"])
        self.assertEqual(2, len(minio.objects))
        self.assertEqual(hashlib.sha256(b"alpha beta").hexdigest(), document["source_object_sha256"])
        self.assertEqual(len(b"alpha beta"), document["source_object_size_bytes"])
        self.assertEqual(len(b"alpha beta"), document["extracted_object_size_bytes"])
        payload = dispatch.call_args.args[1]
        self.assertEqual("", payload["content"])
        self.assertEqual(document["extracted_object_key"], payload["content_object_key"])
        self.assertEqual(document["extracted_text_sha256"], payload["content_object_sha256"])
        self.assertFalse(payload["auto_summary"])

    def test_object_storage_health_exposes_durability_controls(self):
        tenant_id = "tenant-api-object-health"
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        minio.make_bucket(storage.bucket)
        with patch.object(api_main, "object_storage", storage):
            response = self._request("GET", "/api/v1/admin/system/object-storage/health", tenant_id)

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("WARN", response.json()["item"]["health"])
        self.assertTrue(response.json()["item"]["bucket_exists"])
        self.assertFalse(response.json()["item"]["lifecycle_configured"])
        self.assertEqual("DISABLED", response.json()["item"]["versioning_status"])
        self.assertIn("capacity", response.json()["item"])
        self.assertIn(response.json()["item"]["capacity"]["health"], {"OK", "UNCONFIGURED", "WARN", "CRITICAL"})
        self.assertIn("physical_capacity", response.json()["item"])
        self.assertFalse(response.json()["item"]["physical_capacity"]["write_admission_enforced"])

    def test_prometheus_minio_physical_capacity_snapshot_uses_official_metrics(self):
        metric_values = {
            "minio_cluster_capacity_usable_free_bytes": "15",
            "minio_cluster_capacity_usable_total_bytes": "100",
            "minio_cluster_capacity_raw_free_bytes": "30",
            "minio_cluster_capacity_raw_total_bytes": "200",
            "minio_cluster_drive_offline_total": "0",
            "minio_cluster_drive_online_total": "1",
            "minio_cluster_nodes_offline_total": "0",
            "minio_cluster_nodes_online_total": "1",
            "up": "1",
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"__name__": name, "job": "minio-cluster"}, "value": [1775200000, value]}
                    for name, value in metric_values.items()
                ]
            },
        }
        with (
            patch.dict(
                api_main.os.environ,
                {
                    "PROMETHEUS_URL": "http://prometheus.test:9090",
                    "MINIO_PHYSICAL_CAPACITY_WARN_FREE_RATIO": "0.20",
                    "MINIO_PHYSICAL_CAPACITY_CRITICAL_FREE_RATIO": "0.10",
                },
            ),
            patch.object(api_main.httpx, "get", return_value=response) as query,
        ):
            item = api_main._minio_physical_capacity_snapshot()

        self.assertEqual("WARN", item["health"])
        self.assertEqual(15, item["usable_free_bytes"])
        self.assertEqual(100, item["usable_total_bytes"])
        self.assertEqual(0.15, item["usable_free_ratio"])
        self.assertEqual(1, item["drive_online"])
        self.assertTrue(item["scrape_up"])
        self.assertEqual("http://prometheus.test:9090/api/v1/query", query.call_args.args[0])

    def test_physical_capacity_warning_is_exposed_by_unified_alert_preview(self):
        snapshot = {
            "health": "WARN",
            "health_reasons": ["MinIO usable free capacity reached the warning threshold"],
            "configured": True,
            "usable_free_ratio": 0.15,
        }
        with patch.object(api_main, "_minio_physical_capacity_snapshot", return_value=snapshot):
            response = self._request("GET", "/api/v1/admin/system/alerts/preview", "tenant-physical-alert")

        alerts = [item for item in response.json()["items"] if item["alert_type"] == "minio_physical_capacity"]
        self.assertEqual(1, len(alerts))
        self.assertEqual("warning", alerts[0]["severity"])
        self.assertEqual("platform/minio", alerts[0]["scope"])

    def test_alertmanager_webhook_requires_its_own_token_and_persists_resolution(self):
        fingerprint = "api-alertmanager-fingerprint"
        firing = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "AgentPowflowMinioDriveOffline",
                        "component": "minio",
                        "severity": "critical",
                        "instance": "minio:9000",
                    },
                    "annotations": {"summary": "MinIO drive is offline"},
                    "startsAt": "2026-08-03T12:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "fingerprint": fingerprint,
                }
            ],
        }
        with patch.object(api_main, "ALERTMANAGER_WEBHOOK_TOKEN", "alertmanager-test-token"):
            denied = self._request(
                "POST",
                "/api/v1/internal/alerts/alertmanager",
                "platform",
                headers={"Authorization": "Bearer wrong-token"},
                json=firing,
            )
            accepted = self._request(
                "POST",
                "/api/v1/internal/alerts/alertmanager",
                "platform",
                headers={"Authorization": "Bearer alertmanager-test-token"},
                json=firing,
            )
            preview = self._request("GET", "/api/v1/admin/system/alerts/preview", "platform")
            resolved_payload = {
                **firing,
                "status": "resolved",
                "alerts": [{**firing["alerts"][0], "status": "resolved", "endsAt": "2026-08-03T12:05:00Z"}],
            }
            resolved = self._request(
                "POST",
                "/api/v1/internal/alerts/alertmanager",
                "platform",
                headers={"Authorization": "Bearer alertmanager-test-token"},
                json=resolved_payload,
            )

        self.assertEqual(401, denied.status_code)
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertEqual(1, accepted.json()["accepted"])
        durable = [
            item for item in preview.json()["items"]
            if item["alert_type"] == "alertmanager" and item["raw"]["fingerprint"] == fingerprint
        ]
        self.assertEqual(1, len(durable))
        self.assertEqual("critical", durable[0]["severity"])
        self.assertEqual(200, resolved.status_code, resolved.text)
        self.assertEqual([], [row for row in self.store.list_alertmanager_alerts(status="FIRING") if row["fingerprint"] == fingerprint])

    def test_multipart_upload_rejects_quota_before_writing_objects(self):
        tenant_id = "tenant-api-quota"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Quota KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with (
            patch.object(api_main, "object_storage", storage),
            patch.dict(api_main.os.environ, {"OBJECT_STORAGE_TENANT_QUOTA_BYTES": "10"}),
            patch.object(api_main, "_send_persistent_task") as dispatch,
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("quota.txt", b"123456", "text/plain")},
                data={"process_mode": "UPLOAD_ONLY"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("rejected", response.json()["status"])
        skipped = response.json()["skipped"][0]
        self.assertEqual("OBJECT_STORAGE_QUOTA_EXCEEDED", skipped["error_code"])
        self.assertEqual(12, skipped["details"]["requested_bytes"])
        self.assertEqual(10, skipped["details"]["quota_bytes"])
        self.assertEqual({}, minio.objects)
        dispatch.assert_not_called()
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*)::bigint AS count FROM storage_quota_reservations WHERE tenant_id=%s",
                (tenant_id,),
            )
            self.assertEqual(0, int(cur.fetchone()["count"]))

    def test_capacity_check_failure_is_safe_and_does_not_write_minio(self):
        tenant_id = "tenant-api-capacity-unavailable"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Capacity Failure KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        with (
            patch.object(api_main, "object_storage", storage),
            patch.object(self.store, "reserve_storage_quota", side_effect=RuntimeError("synthetic database outage")),
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("capacity.txt", b"safe rejection", "text/plain")},
                data={"process_mode": "UPLOAD_ONLY"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("OBJECT_STORAGE_CAPACITY_UNAVAILABLE", response.json()["skipped"][0]["error_code"])
        self.assertEqual({}, minio.objects)

    def test_physical_capacity_critical_rejects_before_quota_or_minio_write(self):
        tenant_id = "tenant-api-physical-critical"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Physical Capacity KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        snapshot = {
            "health": "CRITICAL",
            "reason_codes": ["MINIO_CAPACITY_CRITICAL"],
            "usable_free_bytes": 5,
            "usable_total_bytes": 100,
            "usable_free_ratio": 0.05,
        }
        with (
            patch.object(api_main, "object_storage", storage),
            patch.object(api_main, "_minio_physical_capacity_snapshot", return_value=snapshot),
            patch.dict(api_main.os.environ, {"MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES": "true"}),
            patch.object(self.store, "reserve_storage_quota") as reserve,
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("physical.txt", b"safe rejection", "text/plain")},
                data={"process_mode": "UPLOAD_ONLY"},
            )

        self.assertEqual(200, response.status_code, response.text)
        skipped = response.json()["skipped"][0]
        self.assertEqual("OBJECT_STORAGE_PHYSICAL_CAPACITY_CRITICAL", skipped["error_code"])
        self.assertEqual(507, skipped["status_code"])
        self.assertEqual({}, minio.objects)
        reserve.assert_not_called()

    def test_physical_capacity_monitoring_failure_fails_closed_when_enforced(self):
        tenant_id = "tenant-api-physical-unavailable"
        kb_id = "kb-physical-unavailable"
        self.store.ensure_kb(tenant_id, kb_id)
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        snapshot = {"health": "CRITICAL", "reason_codes": ["PROMETHEUS_QUERY_FAILED"]}
        with (
            patch.object(api_main, "object_storage", storage),
            patch.object(api_main, "_minio_physical_capacity_snapshot", return_value=snapshot),
            patch.dict(api_main.os.environ, {"MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES": "true"}),
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/ingest",
                tenant_id,
                json={"doc_id": "doc-physical-unavailable", "filename": "physical.txt", "content": "safe"},
            )

        self.assertEqual(503, response.status_code, response.text)
        self.assertEqual("OBJECT_STORAGE_PHYSICAL_CAPACITY_UNAVAILABLE", response.json()["detail"]["code"])
        self.assertEqual({}, minio.objects)

    def test_direct_ingest_rejects_text_byte_limit_before_creating_kb_or_lock(self):
        tenant_id = "tenant-api-text-limit"
        kb_id = "kb-text-limit"
        with patch.dict(api_main.os.environ, {"INGEST_MAX_TEXT_BYTES": "1024"}):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/ingest",
                tenant_id,
                json={"doc_id": "doc-text-limit", "filename": "large.txt", "content": "x" * 1025},
            )

        self.assertEqual(413, response.status_code, response.text)
        self.assertEqual("INGEST_TEXT_TOO_LARGE", response.json()["detail"]["code"])
        self.assertIsNone(self.store.get_kb(tenant_id, kb_id))
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*)::bigint AS count FROM task_dedup_locks WHERE tenant_id=%s",
                (tenant_id,),
            )
            self.assertEqual(0, int(cur.fetchone()["count"]))

    def test_direct_ingest_quota_rejection_releases_submission_lock(self):
        tenant_id = "tenant-api-ingest-quota"
        kb_id = "kb-ingest-quota"
        self.store.ensure_kb(tenant_id, kb_id)
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        with (
            patch.object(api_main, "object_storage", storage),
            patch.dict(api_main.os.environ, {"OBJECT_STORAGE_TENANT_QUOTA_BYTES": "10"}),
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/ingest",
                tenant_id,
                json={"doc_id": "doc-ingest-quota", "filename": "quota.txt", "content": "123456"},
            )

        self.assertEqual(507, response.status_code, response.text)
        self.assertEqual("OBJECT_STORAGE_QUOTA_EXCEEDED", response.json()["detail"]["code"])
        self.assertEqual({}, minio.objects)
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*)::bigint AS count FROM task_dedup_locks WHERE tenant_id=%s AND doc_id=%s",
                (tenant_id, "doc-ingest-quota"),
            )
            self.assertEqual(0, int(cur.fetchone()["count"]))

    def test_capacity_health_metrics_and_alert_preview_share_threshold_state(self):
        tenant_id = "tenant-api-capacity-alert"
        kb_id = "kb-capacity-alert"
        doc_id = "doc-capacity-alert"
        self.store.save_document(tenant_id, kb_id, doc_id, "capacity.txt", "payload")
        self.store.set_document_object_metadata(
            tenant_id,
            kb_id,
            doc_id,
            source_bucket="documents",
            source_key="capacity-source",
            source_sha256="c" * 64,
            source_size_bytes=60,
            source_content_type="text/plain",
            extracted_key="capacity-extracted",
            extracted_sha256="d" * 64,
            extracted_size_bytes=25,
        )
        env = {"OBJECT_STORAGE_TENANT_QUOTA_BYTES": "100", "OBJECT_STORAGE_QUOTA_WARN_RATIO": "0.8"}
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")
        minio.make_bucket(storage.bucket)
        with patch.dict(api_main.os.environ, env), patch.object(api_main, "object_storage", storage):
            health = self._request("GET", "/api/v1/admin/system/object-storage/health", tenant_id)
            metrics = self._request("GET", "/api/v1/admin/system/cross-store-repairs/metrics", tenant_id)
            alerts = self._request("GET", "/api/v1/admin/system/alerts/preview", tenant_id)

        capacity = health.json()["item"]["capacity"]
        self.assertEqual("WARN", capacity["health"])
        row = next(item for item in capacity["items"] if item["tenant_id"] == tenant_id)
        self.assertEqual(85, row["used_bytes"])
        self.assertEqual(0.85, row["usage_ratio"])
        self.assertIn("agent_powflow_object_storage_max_tenant_usage_ratio", metrics.text)
        capacity_alerts = [item for item in alerts.json()["items"] if item["alert_type"] == "object_storage_capacity"]
        self.assertEqual(1, len(capacity_alerts))
        self.assertEqual("warning", capacity_alerts[0]["severity"])

    def test_partial_minio_upload_is_compensated_and_never_dispatched(self):
        tenant_id = "tenant-api-object-failure"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Object Failure KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        minio.fail_put_after = 1
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with patch.object(api_main, "object_storage", storage), patch.object(api_main, "_send_persistent_task") as dispatch:
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("broken.txt", b"must be compensated", "text/plain")},
                data={"process_mode": "UPLOAD_CLEAN_CHUNK_EMBED", "chunk_method": "sentence"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(0, response.json()["accepted_count"])
        self.assertEqual(1, response.json()["skipped_count"])
        self.assertEqual({}, minio.objects)
        self.assertEqual([], self.store.list_documents(tenant_id, kb_id))
        dispatch.assert_not_called()

    def test_multipart_upload_rejects_oversized_file_with_bounded_read(self):
        tenant_id = "tenant-api-upload-limit"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Upload Limit KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with (
            patch.object(api_main, "object_storage", storage),
            patch.object(self.store, "get_upload_settings", return_value={"max_file_size_mb": 1}),
            patch.object(api_main, "_send_persistent_task") as dispatch,
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files={"files": ("oversized.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("rejected", response.json()["status"])
        self.assertIn("file too large", response.json()["skipped"][0]["reason"])
        self.assertEqual({}, minio.objects)
        dispatch.assert_not_called()

    def test_multipart_upload_enforces_file_count_without_reading_extra_files(self):
        tenant_id = "tenant-api-upload-count"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Upload Count KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with (
            patch.object(api_main, "object_storage", storage),
            patch.dict(api_main.os.environ, {"UPLOAD_MAX_FILES_PER_REQUEST": "1"}),
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files=[
                    ("files", ("first.txt", b"first", "text/plain")),
                    ("files", ("second.txt", b"second", "text/plain")),
                ],
                data={"process_mode": "UPLOAD_ONLY"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["accepted_count"])
        self.assertEqual(1, response.json()["skipped_count"])
        self.assertIn("too many files", response.json()["skipped"][0]["reason"])
        self.assertEqual(1, response.json()["max_files_per_request"])

    def test_multipart_upload_enforces_total_batch_bytes(self):
        tenant_id = "tenant-api-upload-batch"
        created = self._request("POST", "/api/v1/admin/kbs", tenant_id, json={"name": "Upload Batch KB"})
        kb_id = created.json()["item"]["kb_id"]
        minio = InMemoryMinio()
        storage = ObjectStorage(client=minio, bucket="agent-powflow-tests")

        with (
            patch.object(api_main, "object_storage", storage),
            patch.dict(api_main.os.environ, {"UPLOAD_MAX_BATCH_BYTES": "1024"}),
        ):
            response = self._request(
                "POST",
                f"/api/v1/admin/kbs/{kb_id}/documents/upload",
                tenant_id,
                files=[
                    ("files", ("first.txt", b"a" * 700, "text/plain")),
                    ("files", ("second.txt", b"b" * 700, "text/plain")),
                ],
                data={"process_mode": "UPLOAD_ONLY"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["accepted_count"])
        self.assertEqual(1, response.json()["skipped_count"])
        self.assertIn("upload batch too large", response.json()["skipped"][0]["reason"])
        self.assertEqual(1024, response.json()["max_batch_size_bytes"])
