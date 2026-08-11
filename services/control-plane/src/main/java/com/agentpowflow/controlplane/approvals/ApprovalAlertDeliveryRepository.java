package com.agentpowflow.controlplane.approvals;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface ApprovalAlertDeliveryRepository extends JpaRepository<ApprovalAlertDeliveryEntity, String> {
    Optional<ApprovalAlertDeliveryEntity> findByAlertId(String alertId);
    Optional<ApprovalAlertDeliveryEntity> findByDeliveryIdAndTenantId(String deliveryId, String tenantId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select d from ApprovalAlertDeliveryEntity d where d.deliveryId = :deliveryId")
    Optional<ApprovalAlertDeliveryEntity> lockByDeliveryId(@Param("deliveryId") String deliveryId);

    @Query(value = """
            SELECT * FROM approval_alert_deliveries
            WHERE status IN ('PENDING', 'RETRY')
              AND available_at <= :now
              AND (locked_until IS NULL OR locked_until < :now)
            ORDER BY available_at, created_at
            LIMIT :batchSize
            FOR UPDATE SKIP LOCKED
            """, nativeQuery = true)
    List<ApprovalAlertDeliveryEntity> lockDispatchable(
            @Param("now") Instant now, @Param("batchSize") int batchSize
    );

    @Query(value = """
            SELECT COUNT(*) FROM approval_alert_deliveries
            WHERE tenant_id = :tenantId AND status = 'DEAD_LETTER'
            """, nativeQuery = true)
    long countDeadLetters(@Param("tenantId") String tenantId);
}
