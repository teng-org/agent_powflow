package com.agentpowflow.controlplane.runs;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;
import java.util.*;
public interface RunDeadLetterRepository extends JpaRepository<RunDeadLetterEntity,String>{
 Optional<RunDeadLetterEntity> findByFailedTaskId(String failedTaskId);
 Optional<RunDeadLetterEntity> findByDeadLetterIdAndTenantId(String id,String tenantId);
 List<RunDeadLetterEntity> findTop100ByTenantIdOrderByCreatedAtDesc(String tenantId);
 List<RunDeadLetterEntity> findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(String tenantId,String status);
 List<RunDeadLetterEntity> findByRunIdAndStatus(String runId,String status);
 @Lock(LockModeType.PESSIMISTIC_WRITE) @Query("select d from RunDeadLetterEntity d where d.deadLetterId=:id") Optional<RunDeadLetterEntity> lockById(@Param("id") String id);
}
