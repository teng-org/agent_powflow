package com.agentpowflow.controlplane.knowledge;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface KnowledgeBaseRepository extends JpaRepository<KnowledgeBaseEntity, KnowledgeBaseId> {
    List<KnowledgeBaseEntity> findByTenantIdOrderByCreatedAtAsc(String tenantId);
    List<KnowledgeBaseEntity> findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(String tenantId, String ownerUserId);
    List<KnowledgeBaseEntity> findByTenantIdAndVisibilityOrderByUpdatedAtDesc(String tenantId, String visibility);
    Optional<KnowledgeBaseEntity> findByTenantIdAndKbId(String tenantId, String kbId);

    @Query(value = "SELECT pg_advisory_xact_lock(hashtextextended(:tenantId, 0))", nativeQuery = true)
    void lockTenant(@Param("tenantId") String tenantId);
}
