package com.agentpowflow.controlplane.product;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentRepository extends JpaRepository<AgentEntity, String> {
    List<AgentEntity> findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(String tenantId, String ownerUserId);
    Optional<AgentEntity> findByAgentIdAndTenantId(String agentId, String tenantId);
    long countByTenantIdAndWorkflowIdAndStatusNot(String tenantId, String workflowId, String status);
}
