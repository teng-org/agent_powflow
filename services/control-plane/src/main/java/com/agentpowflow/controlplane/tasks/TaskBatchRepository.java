package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface TaskBatchRepository extends JpaRepository<TaskBatchEntity, String> {
    Optional<TaskBatchEntity> findByBatchIdAndTenantId(String batchId, String tenantId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select b from TaskBatchEntity b where b.batchId=:batchId and b.tenantId=:tenantId")
    Optional<TaskBatchEntity> lockByBatchIdAndTenantId(
            @Param("batchId") String batchId, @Param("tenantId") String tenantId
    );
}
