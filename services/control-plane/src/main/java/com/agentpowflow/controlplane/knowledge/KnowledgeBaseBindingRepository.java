package com.agentpowflow.controlplane.knowledge;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface KnowledgeBaseBindingRepository extends JpaRepository<KnowledgeBaseBindingEntity, KnowledgeBaseBindingId> {
    List<KnowledgeBaseBindingEntity> findByOwnerTenantIdAndKbIdOrderByUpdatedAtDesc(String ownerTenantId, String kbId);
    List<KnowledgeBaseBindingEntity> findByTargetTenantIdOrderByUpdatedAtDesc(String targetTenantId);
    void deleteByOwnerTenantIdAndKbId(String ownerTenantId, String kbId);
}
