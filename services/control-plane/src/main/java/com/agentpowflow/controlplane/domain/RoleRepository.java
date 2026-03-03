package com.agentpowflow.controlplane.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface RoleRepository extends JpaRepository<RoleEntity, String> {
    Optional<RoleEntity> findByTenantIdAndCode(String tenantId, String code);
    List<RoleEntity> findByTenantId(String tenantId);
}

