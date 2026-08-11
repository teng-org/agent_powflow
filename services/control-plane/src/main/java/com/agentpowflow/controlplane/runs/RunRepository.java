package com.agentpowflow.controlplane.runs;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface RunRepository extends JpaRepository<RunEntity, String> {
    Optional<RunEntity> findByRunIdAndTenantIdAndCreatedBy(String runId, String tenantId, String createdBy);
    Optional<RunEntity> findByRunIdAndTenantId(String runId, String tenantId);
    List<RunEntity> findByConversationIdAndTenantId(String conversationId, String tenantId);
    List<RunEntity> findTop100ByTenantIdOrderByCreatedAtDesc(String tenantId);
    List<RunEntity> findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(String tenantId, String status);
    List<RunEntity> findTop1000ByStatusOrderByCreatedAtAsc(String status);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select r from RunEntity r where r.runId = :runId and r.tenantId = :tenantId")
    Optional<RunEntity> lockByRunIdAndTenantId(@Param("runId") String runId, @Param("tenantId") String tenantId);
}
