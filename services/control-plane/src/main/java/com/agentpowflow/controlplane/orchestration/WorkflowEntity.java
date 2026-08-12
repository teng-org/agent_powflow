package com.agentpowflow.controlplane.orchestration;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "workflows")
public class WorkflowEntity {
    @Id
    @Column(name = "workflow_id", nullable = false, length = 64)
    private String workflowId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "name", nullable = false, length = 128)
    private String name;
    @Column(name = "description", nullable = false, length = 1000)
    private String description = "";
    @Column(name = "status", nullable = false, length = 32)
    private String status = "DRAFT";
    @Column(name = "latest_version_no", nullable = false)
    private int latestVersionNo;
    @Column(name = "draft_dsl_json", nullable = false, columnDefinition = "text")
    private String draftDslJson = "{}";
    @Column(name = "draft_layout_json", nullable = false, columnDefinition = "text")
    private String draftLayoutJson = "{}";
    @Column(name = "created_by", nullable = false, length = 128)
    private String createdBy;
    @Column(name = "updated_by", nullable = false, length = 128)
    private String updatedBy;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version
    @Column(name = "row_version", nullable = false)
    private long rowVersion;

    @PrePersist
    void prePersist() {
        if (workflowId == null || workflowId.isBlank()) workflowId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getWorkflowId() { return workflowId; }
    public void setWorkflowId(String workflowId) { this.workflowId = workflowId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public int getLatestVersionNo() { return latestVersionNo; }
    public void setLatestVersionNo(int latestVersionNo) { this.latestVersionNo = latestVersionNo; }
    public String getDraftDslJson() { return draftDslJson; }
    public void setDraftDslJson(String draftDslJson) { this.draftDslJson = draftDslJson; }
    public String getDraftLayoutJson() { return draftLayoutJson; }
    public void setDraftLayoutJson(String draftLayoutJson) { this.draftLayoutJson = draftLayoutJson; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public String getUpdatedBy() { return updatedBy; }
    public void setUpdatedBy(String updatedBy) { this.updatedBy = updatedBy; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
