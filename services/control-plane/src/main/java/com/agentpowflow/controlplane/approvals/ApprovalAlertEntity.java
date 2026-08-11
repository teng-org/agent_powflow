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
@Table(name = "approval_alerts")
public class ApprovalAlertEntity {
    @Id @Column(name = "alert_id", nullable = false, length = 64)
    private String alertId;
    @Column(name = "dedupe_key", nullable = false, length = 256)
    private String dedupeKey;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "approval_id", nullable = false, length = 64)
    private String approvalId;
    @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "node_id", nullable = false, length = 128)
    private String nodeId;
    @Column(name = "alert_type", nullable = false, length = 32)
    private String alertType;
    @Column(name = "severity", nullable = false, length = 16)
    private String severity;
    @Column(name = "message", nullable = false, length = 500)
    private String message;
    @Column(name = "assignee_roles_json", nullable = false, columnDefinition = "text")
    private String assigneeRolesJson;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "OPEN";
    @Column(name = "opened_at", nullable = false)
    private Instant openedAt;
    @Column(name = "acknowledged_at")
    private Instant acknowledgedAt;
    @Column(name = "acknowledged_by", nullable = false, length = 128)
    private String acknowledgedBy = "";
    @Column(name = "resolved_at")
    private Instant resolvedAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false)
    private long rowVersion;

    @PrePersist void prePersist() {
        if (alertId == null || alertId.isBlank()) alertId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (openedAt == null) openedAt = now;
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getAlertId() { return alertId; }
    public void setAlertId(String value) { alertId = value; }
    public String getDedupeKey() { return dedupeKey; }
    public void setDedupeKey(String value) { dedupeKey = value; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getApprovalId() { return approvalId; }
    public void setApprovalId(String value) { approvalId = value; }
    public String getRunId() { return runId; }
    public void setRunId(String value) { runId = value; }
    public String getNodeId() { return nodeId; }
    public void setNodeId(String value) { nodeId = value; }
    public String getAlertType() { return alertType; }
    public void setAlertType(String value) { alertType = value; }
    public String getSeverity() { return severity; }
    public void setSeverity(String value) { severity = value; }
    public String getMessage() { return message; }
    public void setMessage(String value) { message = value; }
    public String getAssigneeRolesJson() { return assigneeRolesJson; }
    public void setAssigneeRolesJson(String value) { assigneeRolesJson = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public Instant getOpenedAt() { return openedAt; }
    public void setOpenedAt(Instant value) { openedAt = value; }
    public Instant getAcknowledgedAt() { return acknowledgedAt; }
    public void setAcknowledgedAt(Instant value) { acknowledgedAt = value; }
    public String getAcknowledgedBy() { return acknowledgedBy; }
    public void setAcknowledgedBy(String value) { acknowledgedBy = value; }
    public Instant getResolvedAt() { return resolvedAt; }
    public void setResolvedAt(Instant value) { resolvedAt = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
