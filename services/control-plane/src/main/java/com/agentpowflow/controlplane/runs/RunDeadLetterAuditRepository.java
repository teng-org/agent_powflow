package com.agentpowflow.controlplane.runs;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
public interface RunDeadLetterAuditRepository extends JpaRepository<RunDeadLetterAuditEntity,String>{
 List<RunDeadLetterAuditEntity> findByDeadLetterIdOrderByCreatedAtAsc(String deadLetterId);
}
