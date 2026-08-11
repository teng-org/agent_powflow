package com.agentpowflow.controlplane.orchestration;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface WorkflowVersionRepository extends JpaRepository<WorkflowVersionEntity, String> {
    List<WorkflowVersionEntity> findByWorkflowIdAndTenantIdOrderByVersionNoDesc(String workflowId, String tenantId);
    Optional<WorkflowVersionEntity> findByWorkflowIdAndTenantIdAndVersionNo(String workflowId, String tenantId, int versionNo);
}
