package com.agentpowflow.controlplane.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "deployment_api_keys")
public class DeploymentApiKeyEntity {
    @Id
    @Column(name = "api_key_id", nullable = false, length = 64)
    private String apiKeyId;
    @Column(name = "deployment_id", nullable = false, length = 64)
    private String deploymentId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "owner_user_id", nullable = false, length = 128)
    private String ownerUserId;
    @Column(name = "name", nullable = false, length = 128)
    private String name;
    @Column(name = "key_prefix", nullable = false, length = 24, unique = true)
    private String keyPrefix;
    @Column(name = "secret_hash", nullable = false, length = 255)
    private String secretHash;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "ACTIVE";
    @Column(name = "expires_at")
    private Instant expiresAt;
    @Column(name = "last_used_at")
    private Instant lastUsedAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "revoked_at")
    private Instant revokedAt;

    @PrePersist
    void prePersist() {
        if (apiKeyId == null || apiKeyId.isBlank()) apiKeyId = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = Instant.now();
    }

    public String getApiKeyId() { return apiKeyId; }
    public String getDeploymentId() { return deploymentId; }
    public void setDeploymentId(String deploymentId) { this.deploymentId = deploymentId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getOwnerUserId() { return ownerUserId; }
    public void setOwnerUserId(String ownerUserId) { this.ownerUserId = ownerUserId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getKeyPrefix() { return keyPrefix; }
    public void setKeyPrefix(String keyPrefix) { this.keyPrefix = keyPrefix; }
    public String getSecretHash() { return secretHash; }
    public void setSecretHash(String secretHash) { this.secretHash = secretHash; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant expiresAt) { this.expiresAt = expiresAt; }
    public Instant getLastUsedAt() { return lastUsedAt; }
    public void setLastUsedAt(Instant lastUsedAt) { this.lastUsedAt = lastUsedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getRevokedAt() { return revokedAt; }
    public void setRevokedAt(Instant revokedAt) { this.revokedAt = revokedAt; }
}
