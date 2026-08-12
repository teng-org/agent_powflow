package com.agentpowflow.controlplane.product;

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
@Table(name = "agents")
public class AgentEntity {
    @Id
    @Column(name = "agent_id", nullable = false, length = 64)
    private String agentId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "owner_user_id", nullable = false, length = 128)
    private String ownerUserId;
    @Column(name = "name", nullable = false, length = 128)
    private String name;
    @Column(name = "description", nullable = false, length = 1000)
    private String description = "";
    @Column(name = "instructions", nullable = false, columnDefinition = "text")
    private String instructions = "";
    @Column(name = "model_config_json", nullable = false, columnDefinition = "text")
    private String modelConfigJson = "{}";
    @Column(name = "knowledge_base_ids_json", nullable = false, columnDefinition = "text")
    private String knowledgeBaseIdsJson = "[]";
    @Column(name = "workflow_id", length = 64)
    private String workflowId;
    @Column(name = "visibility", nullable = false, length = 32)
    private String visibility = "PRIVATE";
    @Column(name = "status", nullable = false, length = 32)
    private String status = "DRAFT";
    @Column(name = "latest_version_no", nullable = false)
    private int latestVersionNo;
    @Version
    @Column(name = "row_version", nullable = false)
    private long rowVersion;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    void prePersist() {
        if (agentId == null || agentId.isBlank()) agentId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getAgentId() { return agentId; }
    public void setAgentId(String agentId) { this.agentId = agentId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getOwnerUserId() { return ownerUserId; }
    public void setOwnerUserId(String ownerUserId) { this.ownerUserId = ownerUserId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getInstructions() { return instructions; }
    public void setInstructions(String instructions) { this.instructions = instructions; }
    public String getModelConfigJson() { return modelConfigJson; }
    public void setModelConfigJson(String modelConfigJson) { this.modelConfigJson = modelConfigJson; }
    public String getKnowledgeBaseIdsJson() { return knowledgeBaseIdsJson; }
    public void setKnowledgeBaseIdsJson(String knowledgeBaseIdsJson) { this.knowledgeBaseIdsJson = knowledgeBaseIdsJson; }
    public String getWorkflowId() { return workflowId; }
    public void setWorkflowId(String workflowId) { this.workflowId = workflowId; }
    public String getVisibility() { return visibility; }
    public void setVisibility(String visibility) { this.visibility = visibility; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public int getLatestVersionNo() { return latestVersionNo; }
    public void setLatestVersionNo(int latestVersionNo) { this.latestVersionNo = latestVersionNo; }
    public long getRowVersion() { return rowVersion; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
