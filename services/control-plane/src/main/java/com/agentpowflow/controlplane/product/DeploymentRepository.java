package com.agentpowflow.controlplane.product;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface DeploymentRepository extends JpaRepository<DeploymentEntity, String> {
    List<DeploymentEntity> findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(String tenantId, String ownerUserId);
    Optional<DeploymentEntity> findByDeploymentIdAndTenantId(String deploymentId, String tenantId);
    Optional<DeploymentEntity> findBySlugAndStatus(String slug, String status);
    boolean existsBySlug(String slug);
    long countByTenantIdAndAssetTypeAndAssetIdAndStatusNot(
            String tenantId, String assetType, String assetId, String status
    );
}
