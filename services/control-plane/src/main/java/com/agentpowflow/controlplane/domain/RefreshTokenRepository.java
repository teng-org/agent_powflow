package com.agentpowflow.controlplane.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.time.Instant;
import java.util.Optional;

public interface RefreshTokenRepository extends JpaRepository<RefreshTokenEntity, String> {
    Optional<RefreshTokenEntity> findByTokenHash(String tokenHash);
    void deleteByExpiresAtBefore(Instant now);
    void deleteByTenantIdAndUserId(String tenantId, String userId);
}

