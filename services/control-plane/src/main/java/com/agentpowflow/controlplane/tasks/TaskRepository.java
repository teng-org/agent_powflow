package com.agentpowflow.controlplane.tasks;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import jakarta.persistence.LockModeType;

public interface TaskRepository extends JpaRepository<TaskEntity, String> {
    List<TaskEntity> findByTenantIdOrderByUpdatedAtDesc(String tenantId, Pageable pageable);
    List<TaskEntity> findByTenantIdAndKbIdOrderByUpdatedAtDesc(String tenantId, String kbId, Pageable pageable);
    Optional<TaskEntity> findByTaskIdAndTenantId(String taskId, String tenantId);
    Optional<TaskEntity> findFirstByTenantIdAndKbIdAndDocIdAndTaskTypeAndStatusInOrderByCreatedAtAsc(
            String tenantId, String kbId, String docId, String taskType, Collection<String> statuses
    );
    Optional<TaskEntity> findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
            String tenantId, String kbId, String docId, Collection<String> statuses
    );

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select t from TaskEntity t where t.taskId=:taskId and t.tenantId=:tenantId")
    Optional<TaskEntity> lockByTaskIdAndTenantId(@Param("taskId") String taskId, @Param("tenantId") String tenantId);

    @Query(value = "SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))", nativeQuery = true)
    void lockCommandScope(@Param("scope") String scope);
    List<TaskEntity> findByTenantIdAndBatchIdAndBatchIsParentFalseAndStatusIn(
            String tenantId, String batchId, Collection<String> statuses
    );
    List<TaskEntity> findByTenantIdAndBatchIdOrderByBatchIsParentDescUpdatedAtDesc(String tenantId, String batchId);
    List<TaskEntity> findByTenantIdAndBatchIdAndBatchIsParentFalse(String tenantId, String batchId);
    List<TaskEntity> findByTenantIdAndStatusIn(String tenantId, Collection<String> statuses);

    @Modifying
    @Query(value = "DELETE FROM task_dedup_locks WHERE task_id IN (:taskIds)", nativeQuery = true)
    int deleteDedupLocks(@Param("taskIds") Collection<String> taskIds);

    @Modifying
    @Query(value = "DELETE FROM task_batches b WHERE b.tenant_id=:tenantId " +
            "AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.tenant_id=b.tenant_id AND t.batch_id=b.batch_id)", nativeQuery = true)
    int deleteEmptyBatches(@Param("tenantId") String tenantId);
}
