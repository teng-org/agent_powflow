package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface RunEventRepository extends JpaRepository<RunEventEntity, String> {
    Optional<RunEventEntity> findByTenantIdAndExternalEventId(String tenantId, String externalEventId);
    List<RunEventEntity> findByRunIdAndTenantIdOrderBySequenceNoAsc(String runId, String tenantId);
    void deleteByRunIdAndTenantId(String runId, String tenantId);

    @Query("select coalesce(max(e.sequenceNo), 0) from RunEventEntity e where e.runId = :runId and e.tenantId = :tenantId")
    long maxSequence(@Param("runId") String runId, @Param("tenantId") String tenantId);
}
