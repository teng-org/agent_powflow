package com.agentpowflow.controlplane.approvals;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface ApprovalAlertRepository extends JpaRepository<ApprovalAlertEntity, String> {
    Optional<ApprovalAlertEntity> findByDedupeKey(String dedupeKey);
    Optional<ApprovalAlertEntity> findByAlertIdAndTenantId(String alertId, String tenantId);
    List<ApprovalAlertEntity> findTop100ByTenantIdOrderByOpenedAtDesc(String tenantId);
    List<ApprovalAlertEntity> findTop100ByTenantIdAndStatusOrderByOpenedAtDesc(String tenantId, String status);

    @Modifying
    @Query(value = """
            INSERT INTO approval_alerts (
              alert_id, dedupe_key, tenant_id, approval_id, run_id, node_id,
              alert_type, severity, message, assignee_roles_json, status,
              opened_at, acknowledged_by, created_at, updated_at, row_version
            ) VALUES (
              :alertId, :dedupeKey, :tenantId, :approvalId, :runId, :nodeId,
              :alertType, :severity, :message, :assigneeRolesJson, 'OPEN',
              :now, '', :now, :now, 0
            )
            ON CONFLICT (dedupe_key) DO NOTHING
            """, nativeQuery = true)
    int insertIgnore(
            @Param("alertId") String alertId,
            @Param("dedupeKey") String dedupeKey,
            @Param("tenantId") String tenantId,
            @Param("approvalId") String approvalId,
            @Param("runId") String runId,
            @Param("nodeId") String nodeId,
            @Param("alertType") String alertType,
            @Param("severity") String severity,
            @Param("message") String message,
            @Param("assigneeRolesJson") String assigneeRolesJson,
            @Param("now") Instant now
    );

    @Modifying
    @Query("""
            update ApprovalAlertEntity a
            set a.status = 'RESOLVED', a.resolvedAt = :now
            where a.approvalId = :approvalId and a.status in ('OPEN', 'ACKNOWLEDGED')
              and a.alertType in ('DUE_SOON', 'OVERDUE')
            """)
    int resolveQueueRiskAlerts(@Param("approvalId") String approvalId, @Param("now") Instant now);

    @Modifying
    @Query("""
            update ApprovalAlertEntity a
            set a.status = 'RESOLVED', a.resolvedAt = :now
            where a.approvalId = :approvalId and a.status in ('OPEN', 'ACKNOWLEDGED')
              and a.alertType = 'DUE_SOON'
            """)
    int resolveDueSoonAlert(@Param("approvalId") String approvalId, @Param("now") Instant now);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select a from ApprovalAlertEntity a where a.alertId = :alertId")
    Optional<ApprovalAlertEntity> lockByAlertId(@Param("alertId") String alertId);

    @Query(value = """
            SELECT
              COUNT(*) FILTER (WHERE status = 'OPEN') AS "openCount",
              COUNT(*) FILTER (WHERE status = 'OPEN' AND severity = 'CRITICAL') AS "criticalCount",
              COUNT(*) FILTER (WHERE status = 'ACKNOWLEDGED') AS "acknowledgedCount",
              COUNT(*) FILTER (WHERE status = 'RESOLVED' AND resolved_at >= :windowStart) AS "resolvedCount24h",
              COUNT(*) FILTER (WHERE alert_type = 'TIMED_OUT' AND opened_at >= :windowStart) AS "timedOutCount24h"
            FROM approval_alerts
            WHERE tenant_id = :tenantId
            """, nativeQuery = true)
    ApprovalAlertStatsProjection summarize(@Param("tenantId") String tenantId, @Param("windowStart") Instant windowStart);

    interface ApprovalAlertStatsProjection {
        long getOpenCount();
        long getCriticalCount();
        long getAcknowledgedCount();
        long getResolvedCount24h();
        long getTimedOutCount24h();
    }
}
