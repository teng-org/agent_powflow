package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface MessageRepository extends JpaRepository<MessageEntity, String> {
    List<MessageEntity> findByConversationIdAndTenantIdOrderByCreatedAtAsc(String conversationId, String tenantId);
    Optional<MessageEntity> findByRelatedRunIdAndRole(String relatedRunId, String role);
    void deleteByConversationIdAndTenantId(String conversationId, String tenantId);
}
