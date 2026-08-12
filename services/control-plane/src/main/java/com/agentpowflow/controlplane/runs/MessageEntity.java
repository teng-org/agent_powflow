package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "messages")
public class MessageEntity {
    @Id @Column(name = "message_id", nullable = false, length = 64)
    private String messageId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "conversation_id", nullable = false, length = 64)
    private String conversationId;
    @Column(name = "role", nullable = false, length = 32)
    private String role;
    @Column(name = "content_json", nullable = false, columnDefinition = "text")
    private String contentJson;
    @Column(name = "related_run_id", length = 64)
    private String relatedRunId;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @PrePersist void prePersist() {
        if (messageId == null || messageId.isBlank()) messageId = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = Instant.now();
    }

    public String getMessageId() { return messageId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String value) { conversationId = value; }
    public String getRole() { return role; }
    public void setRole(String value) { role = value; }
    public String getContentJson() { return contentJson; }
    public void setContentJson(String value) { contentJson = value; }
    public String getRelatedRunId() { return relatedRunId; }
    public void setRelatedRunId(String value) { relatedRunId = value; }
    public Instant getCreatedAt() { return createdAt; }
}
