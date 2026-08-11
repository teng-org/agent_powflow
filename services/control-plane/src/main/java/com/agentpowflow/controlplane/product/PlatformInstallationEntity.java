package com.agentpowflow.controlplane.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "platform_installation")
public class PlatformInstallationEntity {
    @Id
    @Column(name = "installation_id", nullable = false, length = 32)
    private String installationId;
    @Column(name = "state", nullable = false, length = 32)
    private String state = "REQUIRED";
    @Column(name = "site_name", nullable = false, length = 128)
    private String siteName = "Powflow";
    @Column(name = "public_console_url", nullable = false, length = 512)
    private String publicConsoleUrl = "";
    @Column(name = "public_api_url", nullable = false, length = 512)
    private String publicApiUrl = "";
    @Column(name = "provider_ready", nullable = false)
    private boolean providerReady;
    @Column(name = "storage_ready", nullable = false)
    private boolean storageReady;
    @Column(name = "completed_by", length = 128)
    private String completedBy;
    @Column(name = "completed_at")
    private Instant completedAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getInstallationId() { return installationId; }
    public String getState() { return state; }
    public void setState(String state) { this.state = state; }
    public String getSiteName() { return siteName; }
    public void setSiteName(String siteName) { this.siteName = siteName; }
    public String getPublicConsoleUrl() { return publicConsoleUrl; }
    public void setPublicConsoleUrl(String publicConsoleUrl) { this.publicConsoleUrl = publicConsoleUrl; }
    public String getPublicApiUrl() { return publicApiUrl; }
    public void setPublicApiUrl(String publicApiUrl) { this.publicApiUrl = publicApiUrl; }
    public boolean isProviderReady() { return providerReady; }
    public void setProviderReady(boolean providerReady) { this.providerReady = providerReady; }
    public boolean isStorageReady() { return storageReady; }
    public void setStorageReady(boolean storageReady) { this.storageReady = storageReady; }
    public String getCompletedBy() { return completedBy; }
    public void setCompletedBy(String completedBy) { this.completedBy = completedBy; }
    public Instant getCompletedAt() { return completedAt; }
    public void setCompletedAt(Instant completedAt) { this.completedAt = completedAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
