package com.agentpowflow.controlplane.product;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface DeploymentApiKeyRepository extends JpaRepository<DeploymentApiKeyEntity, String> {
    List<DeploymentApiKeyEntity> findByTenantIdAndDeploymentIdOrderByCreatedAtDesc(String tenantId, String deploymentId);
    Optional<DeploymentApiKeyEntity> findByApiKeyIdAndTenantId(String apiKeyId, String tenantId);
    Optional<DeploymentApiKeyEntity> findByKeyPrefixAndStatus(String keyPrefix, String status);
    boolean existsByKeyPrefix(String keyPrefix);
}
