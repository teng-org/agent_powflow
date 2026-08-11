package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import jakarta.persistence.Version;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "task_dispatch_outbox", uniqueConstraints = {
        @UniqueConstraint(name = "uk_task_dispatch_outbox_task", columnNames = "task_id")
})
public class TaskDispatchOutboxEntity {
    @Id @Column(name = "outbox_id", nullable = false) private String outboxId;
    @Column(name = "tenant_id", nullable = false) private String tenantId;
    @Column(name = "task_id", nullable = false) private String taskId;
    @Column(name = "command_type", nullable = false) private String commandType;
    @Column(name = "payload_json", nullable = false, columnDefinition = "text") private String payloadJson;
    @Column(nullable = false) private String status = "PENDING";
    @Column(name = "attempt_count", nullable = false) private int attemptCount;
    @Column(name = "max_attempts", nullable = false) private int maxAttempts = 8;
    @Column(name = "available_at", nullable = false) private Instant availableAt;
    @Column(name = "locked_by", nullable = false) private String lockedBy = "";
    @Column(name = "locked_until") private Instant lockedUntil;
    @Column(name = "last_error", nullable = false) private String lastError = "";
    @Column(name = "published_at") private Instant publishedAt;
    @Column(name = "dead_lettered_at") private Instant deadLetteredAt;
    @Column(name = "created_at", nullable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false) private long rowVersion;

    @PrePersist void prePersist() {
        if (outboxId == null || outboxId.isBlank()) outboxId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (availableAt == null) availableAt = now;
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getOutboxId() { return outboxId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }
    public String getCommandType() { return commandType; }
    public void setCommandType(String commandType) { this.commandType = commandType; }
    public String getPayloadJson() { return payloadJson; }
    public void setPayloadJson(String payloadJson) { this.payloadJson = payloadJson; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public int getAttemptCount() { return attemptCount; }
    public void setAttemptCount(int attemptCount) { this.attemptCount = attemptCount; }
    public int getMaxAttempts() { return maxAttempts; }
    public void setMaxAttempts(int maxAttempts) { this.maxAttempts = maxAttempts; }
    public Instant getAvailableAt() { return availableAt; }
    public void setAvailableAt(Instant availableAt) { this.availableAt = availableAt; }
    public String getLockedBy() { return lockedBy; }
    public void setLockedBy(String lockedBy) { this.lockedBy = lockedBy; }
    public Instant getLockedUntil() { return lockedUntil; }
    public void setLockedUntil(Instant lockedUntil) { this.lockedUntil = lockedUntil; }
    public String getLastError() { return lastError; }
    public void setLastError(String lastError) { this.lastError = lastError; }
    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant publishedAt) { this.publishedAt = publishedAt; }
    public Instant getDeadLetteredAt() { return deadLetteredAt; }
    public void setDeadLetteredAt(Instant deadLetteredAt) { this.deadLetteredAt = deadLetteredAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
