package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface TaskDispatchOutboxRepository extends JpaRepository<TaskDispatchOutboxEntity, String> {
    Optional<TaskDispatchOutboxEntity> findByTaskId(String taskId);
    Optional<TaskDispatchOutboxEntity> findByOutboxIdAndTenantId(String outboxId, String tenantId);
    List<TaskDispatchOutboxEntity> findTop100ByTenantIdOrderByCreatedAtDesc(String tenantId);
    List<TaskDispatchOutboxEntity> findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(String tenantId, String status);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from TaskDispatchOutboxEntity o where o.outboxId=:outboxId")
    Optional<TaskDispatchOutboxEntity> lockByOutboxId(@Param("outboxId") String outboxId);

    @Query(value = """
            select * from task_dispatch_outbox
            where status in ('PENDING','RETRY')
              and available_at <= :now
              and (locked_until is null or locked_until < :now)
            order by available_at asc, created_at asc
            limit :batchSize
            for update skip locked
            """, nativeQuery = true)
    List<TaskDispatchOutboxEntity> lockDispatchable(@Param("now") Instant now, @Param("batchSize") int batchSize);

    @Query(value = """
            select * from task_dispatch_outbox
            where status='WAITING_UPLOAD' and updated_at < :cutoff
            order by updated_at asc
            limit :batchSize
            for update skip locked
            """, nativeQuery = true)
    List<TaskDispatchOutboxEntity> lockExpiredUploadIntents(
            @Param("cutoff") Instant cutoff,
            @Param("batchSize") int batchSize
    );

    @Query(value = """
            select * from task_dispatch_outbox
            where status='CLEANUP_PENDING'
            order by updated_at asc
            limit :batchSize
            for update skip locked
            """, nativeQuery = true)
    List<TaskDispatchOutboxEntity> lockPendingUploadCleanup(@Param("batchSize") int batchSize);
}
