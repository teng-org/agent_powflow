package com.agentpowflow.controlplane.orchestration;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface WorkflowRepository extends JpaRepository<WorkflowEntity, String> {
    Optional<WorkflowEntity> findByWorkflowIdAndTenantId(String workflowId, String tenantId);
    List<WorkflowEntity> findByTenantIdOrderByUpdatedAtDesc(String tenantId);
}
