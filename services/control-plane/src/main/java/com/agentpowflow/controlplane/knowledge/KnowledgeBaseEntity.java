package com.agentpowflow.controlplane.knowledge;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@IdClass(KnowledgeBaseId.class)
@Table(name = "knowledge_bases")
public class KnowledgeBaseEntity {
    @Id
    @Column(name = "tenant_id", nullable = false)
    private String tenantId;
    @Id
    @Column(name = "kb_id", nullable = false)
    private String kbId;
    @Column(name = "name", nullable = false)
    private String name;
    @Column(name = "visibility", nullable = false)
    private String visibility = "PRIVATE";
    @Column(name = "summary", nullable = false)
    private String summary = "";
    @Column(name = "description", nullable = false, length = 1000)
    private String description = "";
    @Column(name = "tags_json", nullable = false, columnDefinition = "TEXT")
    private String tagsJson = "[]";
    @Column(name = "status", nullable = false)
    private String status = "ACTIVE";
    @Column(name = "frozen", nullable = false)
    private boolean frozen;
    @Column(name = "metadata_owner", nullable = false)
    private String metadataOwner = "CONTROL";
    @Column(name = "owner_user_id", nullable = false, length = 128)
    private String ownerUserId = "system";
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getVisibility() { return visibility; }
    public void setVisibility(String visibility) { this.visibility = visibility; }
    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getTagsJson() { return tagsJson; }
    public void setTagsJson(String tagsJson) { this.tagsJson = tagsJson; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public boolean isFrozen() { return frozen; }
    public void setFrozen(boolean frozen) { this.frozen = frozen; }
    public String getMetadataOwner() { return metadataOwner; }
    public void setMetadataOwner(String metadataOwner) { this.metadataOwner = metadataOwner; }
    public String getOwnerUserId() { return ownerUserId; }
    public void setOwnerUserId(String ownerUserId) { this.ownerUserId = ownerUserId; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
