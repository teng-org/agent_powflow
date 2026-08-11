package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.List;

public interface RunApprovalAuditFactRepository extends JpaRepository<RunApprovalAuditFactEntity, String> {
    @Query(value = """
            WITH report_days AS (
              SELECT generate_series(CAST(:startDate AS date), CAST(:endDate AS date), INTERVAL '1 day')::date AS day
            ), visible_facts AS (
              SELECT f.*
              FROM approval_audit_facts f
              WHERE f.tenant_id = :tenantId
                AND (
                  :platformAdmin = TRUE
                  OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(f.assignee_roles_json::jsonb) assigned(role_code)
                    WHERE UPPER(assigned.role_code) IN (:actorRoles)
                  )
                )
            )
            SELECT
              d.day AS "date",
              COUNT(f.approval_id) FILTER (
                WHERE (f.created_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "createdCount",
              COUNT(f.approval_id) FILTER (
                WHERE f.status = 'DECIDED' AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "decidedCount",
              COUNT(f.approval_id) FILTER (
                WHERE f.status = 'DECIDED' AND f.decision = 'APPROVE'
                  AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "approvedCount",
              COUNT(f.approval_id) FILTER (
                WHERE f.status = 'DECIDED' AND f.decision = 'REJECT'
                  AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "rejectedCount",
              COUNT(f.approval_id) FILTER (
                WHERE f.status = 'DECIDED' AND f.timed_out = TRUE
                  AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "timedOutCount",
              COUNT(f.approval_id) FILTER (
                WHERE f.status = 'CANCELLED' AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              ) AS "cancelledCount",
              COALESCE(ROUND(AVG(f.decision_duration_seconds) FILTER (
                WHERE f.status = 'DECIDED' AND (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
              )), 0)::BIGINT AS "averageDecisionSeconds"
            FROM report_days d
            LEFT JOIN visible_facts f
              ON (f.created_at AT TIME ZONE 'UTC')::date = d.day
              OR (f.terminal_at AT TIME ZONE 'UTC')::date = d.day
            GROUP BY d.day
            ORDER BY d.day
            """, nativeQuery = true)
    List<ApprovalTrendProjection> summarizeDaily(
            @Param("tenantId") String tenantId,
            @Param("platformAdmin") boolean platformAdmin,
            @Param("actorRoles") List<String> actorRoles,
            @Param("startDate") LocalDate startDate,
            @Param("endDate") LocalDate endDate
    );

    interface ApprovalTrendProjection {
        LocalDate getDate();
        long getCreatedCount();
        long getDecidedCount();
        long getApprovedCount();
        long getRejectedCount();
        long getTimedOutCount();
        long getCancelledCount();
        long getAverageDecisionSeconds();
    }
}
