package com.agentpowflow.controlplane.runs;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ConversationRepository extends JpaRepository<ConversationEntity, String> {
    Optional<ConversationEntity> findByConversationIdAndTenantIdAndCreatedBy(String conversationId, String tenantId, String createdBy);
    List<ConversationEntity> findByTenantIdAndCreatedByOrderByUpdatedAtDesc(String tenantId, String createdBy);
}
