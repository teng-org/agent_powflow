package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface RunNodeExecutionRepository extends JpaRepository<RunNodeExecutionEntity, String> {
    Optional<RunNodeExecutionEntity> findByTenantIdAndRunIdAndNodeId(String tenantId, String runId, String nodeId);
    List<RunNodeExecutionEntity> findByTenantIdAndRunIdOrderByOrdinalAsc(String tenantId, String runId);
}
