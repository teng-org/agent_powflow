package com.agentpowflow.controlplane.approvals;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "approval_alert_deliveries")
public class ApprovalAlertDeliveryEntity {
    @Id @Column(name = "delivery_id", nullable = false, length = 64)
    private String deliveryId;
    @Column(name = "alert_id", nullable = false, length = 64)
    private String alertId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "channel", nullable = false, length = 16)
    private String channel = "WEBHOOK";
    @Column(name = "destination_host", nullable = false, length = 255)
    private String destinationHost = "";
    @Column(name = "payload_json", nullable = false, columnDefinition = "text")
    private String payloadJson;
    @Column(name = "status", nullable = false, length = 32)
    private String status;
    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;
    @Column(name = "max_attempts", nullable = false)
    private int maxAttempts;
    @Column(name = "available_at", nullable = false)
    private Instant availableAt;
    @Column(name = "locked_by", nullable = false, length = 128)
    private String lockedBy = "";
    @Column(name = "locked_until")
    private Instant lockedUntil;
    @Column(name = "last_error", nullable = false, length = 2000)
    private String lastError = "";
    @Column(name = "published_at")
    private Instant publishedAt;
    @Column(name = "dead_lettered_at")
    private Instant deadLetteredAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false)
    private long rowVersion;

    @PrePersist void prePersist() {
        if (deliveryId == null || deliveryId.isBlank()) deliveryId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (availableAt == null) availableAt = now;
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getDeliveryId() { return deliveryId; }
    public void setDeliveryId(String value) { deliveryId = value; }
    public String getAlertId() { return alertId; }
    public void setAlertId(String value) { alertId = value; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getChannel() { return channel; }
    public String getDestinationHost() { return destinationHost; }
    public void setDestinationHost(String value) { destinationHost = value; }
    public String getPayloadJson() { return payloadJson; }
    public void setPayloadJson(String value) { payloadJson = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public int getAttemptCount() { return attemptCount; }
    public void setAttemptCount(int value) { attemptCount = value; }
    public int getMaxAttempts() { return maxAttempts; }
    public void setMaxAttempts(int value) { maxAttempts = value; }
    public Instant getAvailableAt() { return availableAt; }
    public void setAvailableAt(Instant value) { availableAt = value; }
    public String getLockedBy() { return lockedBy; }
    public void setLockedBy(String value) { lockedBy = value; }
    public Instant getLockedUntil() { return lockedUntil; }
    public void setLockedUntil(Instant value) { lockedUntil = value; }
    public String getLastError() { return lastError; }
    public void setLastError(String value) { lastError = value; }
    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant value) { publishedAt = value; }
    public Instant getDeadLetteredAt() { return deadLetteredAt; }
    public void setDeadLetteredAt(Instant value) { deadLetteredAt = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
