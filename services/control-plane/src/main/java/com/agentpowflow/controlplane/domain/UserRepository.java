package com.agentpowflow.controlplane.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface UserRepository extends JpaRepository<UserEntity, String> {
    Optional<UserEntity> findByTenantIdAndEmail(String tenantId, String email);
    List<UserEntity> findByTenantIdOrderByCreatedAtDesc(String tenantId);
}

