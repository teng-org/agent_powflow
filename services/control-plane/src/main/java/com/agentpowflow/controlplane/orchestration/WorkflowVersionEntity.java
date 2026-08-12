package com.agentpowflow.controlplane.orchestration;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "workflow_versions", uniqueConstraints = @UniqueConstraint(name = "uk_workflow_version", columnNames = {"workflow_id", "version_no"}))
public class WorkflowVersionEntity {
    @Id
    @Column(name = "version_id", nullable = false, length = 64)
    private String versionId;
    @Column(name = "workflow_id", nullable = false, length = 64)
    private String workflowId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "version_no", nullable = false)
    private int versionNo;
    @Column(name = "dsl_json", nullable = false, columnDefinition = "text")
    private String dslJson;
    @Column(name = "layout_json", nullable = false, columnDefinition = "text")
    private String layoutJson;
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
    public String getWorkflowId() { return workflowId; }
    public void setWorkflowId(String workflowId) { this.workflowId = workflowId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public int getVersionNo() { return versionNo; }
    public void setVersionNo(int versionNo) { this.versionNo = versionNo; }
    public String getDslJson() { return dslJson; }
    public void setDslJson(String dslJson) { this.dslJson = dslJson; }
    public String getLayoutJson() { return layoutJson; }
    public void setLayoutJson(String layoutJson) { this.layoutJson = layoutJson; }
    public String getChecksum() { return checksum; }
    public void setChecksum(String checksum) { this.checksum = checksum; }
    public String getPublishedBy() { return publishedBy; }
    public void setPublishedBy(String publishedBy) { this.publishedBy = publishedBy; }
    public Instant getPublishedAt() { return publishedAt; }
}
