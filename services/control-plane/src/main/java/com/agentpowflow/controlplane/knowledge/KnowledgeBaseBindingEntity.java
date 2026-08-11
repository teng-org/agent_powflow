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
@IdClass(KnowledgeBaseBindingId.class)
@Table(name = "kb_tenant_bindings")
public class KnowledgeBaseBindingEntity {
    @Id
    @Column(name = "owner_tenant_id", nullable = false)
    private String ownerTenantId;
    @Id
    @Column(name = "kb_id", nullable = false)
    private String kbId;
    @Id
    @Column(name = "target_tenant_id", nullable = false)
    private String targetTenantId;
    @Column(name = "created_by", nullable = false)
    private String createdBy;
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

    public String getOwnerTenantId() { return ownerTenantId; }
    public void setOwnerTenantId(String ownerTenantId) { this.ownerTenantId = ownerTenantId; }
    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }
    public String getTargetTenantId() { return targetTenantId; }
    public void setTargetTenantId(String targetTenantId) { this.targetTenantId = targetTenantId; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
