package com.agentpowflow.controlplane.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface UserRoleRepository extends JpaRepository<UserRoleEntity, Long> {
    List<UserRoleEntity> findByTenantIdAndUserId(String tenantId, String userId);
    void deleteByTenantIdAndUserId(String tenantId, String userId);
}

