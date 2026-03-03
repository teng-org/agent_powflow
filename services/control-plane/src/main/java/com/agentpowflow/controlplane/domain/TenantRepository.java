package com.agentpowflow.controlplane.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface TenantRepository extends JpaRepository<TenantEntity, String> {
    Optional<TenantEntity> findByTenantCode(String tenantCode);
}

