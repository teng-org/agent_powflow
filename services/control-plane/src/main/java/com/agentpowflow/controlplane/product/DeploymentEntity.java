package com.agentpowflow.controlplane.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "deployments")
public class DeploymentEntity {
    @Id
    @Column(name = "deployment_id", nullable = false, length = 64)
    private String deploymentId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "owner_user_id", nullable = false, length = 128)
    private String ownerUserId;
    @Column(name = "name", nullable = false, length = 128)
    private String name;
    @Column(name = "slug", nullable = false, length = 96, unique = true)
    private String slug;
    @Column(name = "asset_type", nullable = false, length = 32)
    private String assetType;
    @Column(name = "asset_id", nullable = false, length = 64)
    private String assetId;
    @Column(name = "asset_version_no", nullable = false)
    private int assetVersionNo;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "ACTIVE";
    @Column(name = "rate_limit_per_minute", nullable = false)
    private int rateLimitPerMinute = 60;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    void prePersist() {
        if (deploymentId == null || deploymentId.isBlank()) deploymentId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getDeploymentId() { return deploymentId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getOwnerUserId() { return ownerUserId; }
    public void setOwnerUserId(String ownerUserId) { this.ownerUserId = ownerUserId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getSlug() { return slug; }
    public void setSlug(String slug) { this.slug = slug; }
    public String getAssetType() { return assetType; }
    public void setAssetType(String assetType) { this.assetType = assetType; }
    public String getAssetId() { return assetId; }
    public void setAssetId(String assetId) { this.assetId = assetId; }
    public int getAssetVersionNo() { return assetVersionNo; }
    public void setAssetVersionNo(int assetVersionNo) { this.assetVersionNo = assetVersionNo; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public int getRateLimitPerMinute() { return rateLimitPerMinute; }
    public void setRateLimitPerMinute(int rateLimitPerMinute) { this.rateLimitPerMinute = rateLimitPerMinute; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
