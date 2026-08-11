package com.agentpowflow.controlplane.product;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentVersionRepository extends JpaRepository<AgentVersionEntity, String> {
    List<AgentVersionEntity> findByTenantIdAndAgentIdOrderByVersionNoDesc(String tenantId, String agentId);
    Optional<AgentVersionEntity> findByTenantIdAndAgentIdAndVersionNo(String tenantId, String agentId, int versionNo);
}
