from __future__ import annotations

from datetime import datetime, timedelta, timezone

from postgres_test_support import IsolatedPostgresCase


class AlertmanagerAlertStoreIntegrationTest(IsolatedPostgresCase):
    def test_firing_alert_is_durable_and_resolved_notification_closes_it(self):
        starts_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.store.upsert_alertmanager_alert(
            fingerprint="fingerprint-capacity",
            alert_name="AgentPowflowMinioUsableCapacityLow",
            component="minio",
            severity="warning",
            status="FIRING",
            labels={"alertname": "AgentPowflowMinioUsableCapacityLow", "instance": "minio:9000"},
            annotations={"summary": "MinIO capacity is low"},
            starts_at=starts_at,
            ends_at=None,
        )
        firing = self.store.list_alertmanager_alerts(status="FIRING")
        self.assertEqual(1, len(firing))
        self.assertEqual("fingerprint-capacity", firing[0]["fingerprint"])
        self.assertEqual("MinIO capacity is low", firing[0]["annotations_json"]["summary"])

        self.store.upsert_alertmanager_alert(
            fingerprint="fingerprint-capacity",
            alert_name="AgentPowflowMinioUsableCapacityLow",
            component="minio",
            severity="warning",
            status="RESOLVED",
            labels={"alertname": "AgentPowflowMinioUsableCapacityLow", "instance": "minio:9000"},
            annotations={"summary": "MinIO capacity recovered"},
            starts_at=starts_at,
            ends_at=datetime.now(timezone.utc),
        )
        self.assertEqual([], self.store.list_alertmanager_alerts(status="FIRING"))
        resolved = self.store.list_alertmanager_alerts(status="RESOLVED")
        self.assertEqual(1, len(resolved))
        self.assertIsNotNone(resolved[0]["resolved_at"])
        self.assertEqual("MinIO capacity recovered", resolved[0]["annotations_json"]["summary"])

    def test_resolved_retention_cleanup_is_bounded(self):
        self.store.upsert_alertmanager_alert(
            fingerprint="fingerprint-old",
            alert_name="OldAlert",
            component="minio",
            severity="warning",
            status="RESOLVED",
            labels={"alertname": "OldAlert"},
            annotations={},
            starts_at=None,
            ends_at=None,
        )
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE alertmanager_alerts SET resolved_at=NOW() - INTERVAL '100 days'
                WHERE fingerprint='fingerprint-old'"""
            )
        self.assertEqual(1, self.store.cleanup_resolved_alertmanager_alerts(retention_days=90))
        remaining = self.store.list_alertmanager_alerts(status="RESOLVED")
        self.assertNotIn("fingerprint-old", {row["fingerprint"] for row in remaining})
