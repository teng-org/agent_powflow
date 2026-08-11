package com.agentpowflow.controlplane.tasks;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface TaskEventRepository extends JpaRepository<TaskEventEntity, Long> {
    List<TaskEventEntity> findByTaskIdAndTenantIdOrderByEventIdDesc(String taskId, String tenantId, Pageable pageable);
    Optional<TaskEventEntity> findByTenantIdAndEventKey(String tenantId, String eventKey);
    long deleteByTenantIdAndTaskIdIn(String tenantId, Collection<String> taskIds);
}
