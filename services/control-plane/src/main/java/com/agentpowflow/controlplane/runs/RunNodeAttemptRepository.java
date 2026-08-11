package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface RunNodeAttemptRepository extends JpaRepository<RunNodeAttemptEntity, String> {
    Optional<RunNodeAttemptEntity> findByTenantIdAndRunIdAndNodeIdAndAttemptNo(
            String tenantId, String runId, String nodeId, int attemptNo
    );
    List<RunNodeAttemptEntity> findByTenantIdAndRunIdOrderByNodeIdAscAttemptNoAsc(String tenantId, String runId);
}
