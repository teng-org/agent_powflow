package com.agentpowflow.controlplane.runs;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface RunApprovalRepository extends JpaRepository<RunApprovalEntity, String> {
    Optional<RunApprovalEntity> findByTenantIdAndRunIdAndNodeId(String tenantId, String runId, String nodeId);
    Optional<RunApprovalEntity> findByApprovalIdAndTenantId(String approvalId, String tenantId);
    Optional<RunApprovalEntity> findTopByTenantIdAndRunIdAndStatusOrderByDecidedAtDesc(
            String tenantId, String runId, String status
    );
    List<RunApprovalEntity> findTop100ByTenantIdOrderByCreatedAtDesc(String tenantId);
    List<RunApprovalEntity> findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(String tenantId, String status);
    List<RunApprovalEntity> findTop100ByStatusAndExpiresAtLessThanEqualOrderByExpiresAtAsc(String status, Instant expiresAt);

    @Query(value = """
            SELECT a.*
            FROM run_approvals a
            WHERE a.tenant_id = :tenantId
              AND (:status = '' OR a.status = :status)
              AND (
                :platformAdmin = TRUE
                OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(a.assignee_roles_json::jsonb) assigned(role_code)
                  WHERE UPPER(assigned.role_code) IN (:actorRoles)
                )
              )
            ORDER BY a.created_at DESC
            LIMIT 100
            """, nativeQuery = true)
    List<RunApprovalEntity> findVisible(
            @Param("tenantId") String tenantId,
            @Param("status") String status,
            @Param("platformAdmin") boolean platformAdmin,
            @Param("actorRoles") List<String> actorRoles
    );

    @Query(value = """
            SELECT
              COUNT(*) FILTER (WHERE a.status = 'PENDING') AS "pendingCount",
              COUNT(*) FILTER (WHERE a.status = 'PENDING' AND a.expires_at <= :now) AS "overdueCount",
              COUNT(*) FILTER (
                WHERE a.status = 'PENDING' AND a.expires_at > :now AND a.expires_at <= :dueSoonAt
              ) AS "dueSoonCount",
              COALESCE(EXTRACT(EPOCH FROM (:now - MIN(a.created_at) FILTER (WHERE a.status = 'PENDING'))), 0)::BIGINT
                AS "oldestPendingAgeSeconds",
              COUNT(*) FILTER (WHERE a.status = 'DECIDED' AND a.decided_at >= :windowStart) AS "decidedCount24h",
              COUNT(*) FILTER (
                WHERE a.status = 'DECIDED' AND a.timed_out = TRUE AND a.decided_at >= :windowStart
              ) AS "timedOutCount24h",
              COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM (a.decided_at - a.created_at))) FILTER (
                WHERE a.status = 'DECIDED' AND a.decided_at >= :windowStart
              )), 0)::BIGINT AS "averageDecisionSeconds24h"
            FROM run_approvals a
            WHERE a.tenant_id = :tenantId
              AND (
                :platformAdmin = TRUE
                OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(a.assignee_roles_json::jsonb) assigned(role_code)
                  WHERE UPPER(assigned.role_code) IN (:actorRoles)
                )
              )
            """, nativeQuery = true)
    ApprovalStatsProjection summarizeVisible(
            @Param("tenantId") String tenantId,
            @Param("platformAdmin") boolean platformAdmin,
            @Param("actorRoles") List<String> actorRoles,
            @Param("now") Instant now,
            @Param("dueSoonAt") Instant dueSoonAt,
            @Param("windowStart") Instant windowStart
    );

    interface ApprovalStatsProjection {
        long getPendingCount();
        long getOverdueCount();
        long getDueSoonCount();
        long getOldestPendingAgeSeconds();
        long getDecidedCount24h();
        long getTimedOutCount24h();
        long getAverageDecisionSeconds24h();
    }

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select a from RunApprovalEntity a where a.approvalId = :approvalId and a.tenantId = :tenantId")
    Optional<RunApprovalEntity> lockByApprovalIdAndTenantId(
            @Param("approvalId") String approvalId, @Param("tenantId") String tenantId
    );
}
