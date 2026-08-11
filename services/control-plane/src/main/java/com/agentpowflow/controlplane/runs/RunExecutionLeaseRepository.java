package com.agentpowflow.controlplane.runs;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface RunExecutionLeaseRepository extends JpaRepository<RunExecutionLeaseEntity, String> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select l from RunExecutionLeaseEntity l where l.runId = :runId")
    Optional<RunExecutionLeaseEntity> lockByRunId(@Param("runId") String runId);
}
