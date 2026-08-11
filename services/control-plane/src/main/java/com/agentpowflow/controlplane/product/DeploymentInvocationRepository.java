package com.agentpowflow.controlplane.product;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface DeploymentInvocationRepository extends JpaRepository<DeploymentInvocationEntity, String> {
    long countByDeploymentIdAndCreatedAtAfter(String deploymentId, Instant createdAfter);
    List<DeploymentInvocationEntity> findTop100ByTenantIdAndDeploymentIdOrderByCreatedAtDesc(String tenantId, String deploymentId);
    List<DeploymentInvocationEntity> findTop200ByOrderByCreatedAtDesc();
    Optional<DeploymentInvocationEntity> findByDeploymentIdAndApiKeyIdAndRunId(
            String deploymentId, String apiKeyId, String runId
    );

    @Query(value = "SELECT pg_advisory_xact_lock(hashtextextended(:deploymentId, 0))", nativeQuery = true)
    void lockDeployment(@Param("deploymentId") String deploymentId);
}
