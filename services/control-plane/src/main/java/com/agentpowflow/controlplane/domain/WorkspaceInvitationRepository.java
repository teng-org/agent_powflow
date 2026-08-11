package com.agentpowflow.controlplane.domain;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface WorkspaceInvitationRepository extends JpaRepository<WorkspaceInvitationEntity, String> {
    List<WorkspaceInvitationEntity> findByTenantIdOrderByCreatedAtDesc(String tenantId);
    List<WorkspaceInvitationEntity> findByTenantIdAndEmailAndStatus(String tenantId, String email, String status);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select i from WorkspaceInvitationEntity i where i.tokenHash = :tokenHash")
    Optional<WorkspaceInvitationEntity> findForUpdateByTokenHash(@Param("tokenHash") String tokenHash);
}
