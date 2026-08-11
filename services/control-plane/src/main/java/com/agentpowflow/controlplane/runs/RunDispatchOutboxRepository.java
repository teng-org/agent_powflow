package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import jakarta.persistence.LockModeType;

public interface RunDispatchOutboxRepository extends JpaRepository<RunDispatchOutboxEntity, String> {
    Optional<RunDispatchOutboxEntity> findByRunId(String runId);
    void deleteByRunId(String runId);
    Optional<RunDispatchOutboxEntity> findByOutboxIdAndTenantId(String outboxId, String tenantId);
    List<RunDispatchOutboxEntity> findTop100ByTenantIdOrderByCreatedAtDesc(String tenantId);
    List<RunDispatchOutboxEntity> findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(String tenantId, String status);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from RunDispatchOutboxEntity o where o.outboxId = :outboxId")
    Optional<RunDispatchOutboxEntity> lockByOutboxId(@Param("outboxId") String outboxId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from RunDispatchOutboxEntity o where o.runId = :runId")
    Optional<RunDispatchOutboxEntity> lockByRunId(@Param("runId") String runId);

    @Query(value = """
            select * from run_dispatch_outbox
            where status in ('PENDING', 'RETRY')
              and available_at <= :now
              and (locked_until is null or locked_until < :now)
            order by available_at asc, created_at asc
            limit :batchSize
            for update skip locked
            """, nativeQuery = true)
    List<RunDispatchOutboxEntity> lockDispatchable(
            @Param("now") Instant now,
            @Param("batchSize") int batchSize
    );
}
