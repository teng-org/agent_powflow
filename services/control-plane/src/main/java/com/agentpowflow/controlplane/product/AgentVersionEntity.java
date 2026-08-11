package com.agentpowflow.controlplane.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "agent_versions")
public class AgentVersionEntity {
    @Id
    @Column(name = "version_id", nullable = false, length = 64)
    private String versionId;
    @Column(name = "agent_id", nullable = false, length = 64)
    private String agentId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "version_no", nullable = false)
    private int versionNo;
    @Column(name = "snapshot_json", nullable = false, columnDefinition = "text")
    private String snapshotJson;
    @Column(name = "checksum", nullable = false, length = 64)
    private String checksum;
    @Column(name = "published_by", nullable = false, length = 128)
    private String publishedBy;
    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @PrePersist
    void prePersist() {
        if (versionId == null || versionId.isBlank()) versionId = UUID.randomUUID().toString();
        if (publishedAt == null) publishedAt = Instant.now();
    }

    public String getVersionId() { return versionId; }
    public String getAgentId() { return agentId; }
    public void setAgentId(String agentId) { this.agentId = agentId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public int getVersionNo() { return versionNo; }
    public void setVersionNo(int versionNo) { this.versionNo = versionNo; }
    public String getSnapshotJson() { return snapshotJson; }
    public void setSnapshotJson(String snapshotJson) { this.snapshotJson = snapshotJson; }
    public String getChecksum() { return checksum; }
    public void setChecksum(String checksum) { this.checksum = checksum; }
    public String getPublishedBy() { return publishedBy; }
    public void setPublishedBy(String publishedBy) { this.publishedBy = publishedBy; }
    public Instant getPublishedAt() { return publishedAt; }
}
