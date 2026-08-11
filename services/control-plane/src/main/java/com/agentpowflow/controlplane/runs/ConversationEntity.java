package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "conversations")
public class ConversationEntity {
    @Id @Column(name = "conversation_id", nullable = false, length = 64)
    private String conversationId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "title", nullable = false, length = 160)
    private String title;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "ACTIVE";
    @Column(name = "created_by", nullable = false, length = 128)
    private String createdBy;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist void prePersist() {
        if (conversationId == null || conversationId.isBlank()) conversationId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getConversationId() { return conversationId; }
    public void setConversationId(String value) { conversationId = value; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getTitle() { return title; }
    public void setTitle(String value) { title = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String value) { createdBy = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
