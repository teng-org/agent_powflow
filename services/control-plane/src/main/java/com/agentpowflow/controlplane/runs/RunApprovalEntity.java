package com.agentpowflow.controlplane.runs;

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
@Table(name = "run_approvals", uniqueConstraints = {
        @UniqueConstraint(name = "uk_run_approval_node", columnNames = {"tenant_id", "run_id", "node_id"})
})
public class RunApprovalEntity {
    @Id @Column(name = "approval_id", nullable = false, length = 64)
    private String approvalId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "node_id", nullable = false, length = 128)
    private String nodeId;
    @Column(name = "node_type", nullable = false, length = 64)
    private String nodeType;
    @Column(name = "title", nullable = false, length = 160)
    private String title;
    @Column(name = "instructions", nullable = false, length = 2000)
    private String instructions = "";
    @Column(name = "assignee_roles_json", nullable = false, columnDefinition = "text")
    private String assigneeRolesJson = "[]";
    @Column(name = "status", nullable = false, length = 32)
    private String status = "PENDING";
    @Column(name = "decision", nullable = false, length = 16)
    private String decision = "";
    @Column(name = "decision_reason", nullable = false, length = 1000)
    private String decisionReason = "";
    @Column(name = "decided_by", nullable = false, length = 128)
    private String decidedBy = "";
    @Column(name = "checkpoint_json", nullable = false, columnDefinition = "text")
    private String checkpointJson;
    @Column(name = "timeout_action", nullable = false, length = 16)
    private String timeoutAction;
    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;
    @Column(name = "decided_at")
    private Instant decidedAt;
    @Column(name = "timed_out", nullable = false)
    private boolean timedOut;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false)
    private long rowVersion;

    @PrePersist void prePersist() {
        if (approvalId == null || approvalId.isBlank()) approvalId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getApprovalId() { return approvalId; }
    public void setApprovalId(String value) { approvalId = value; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getRunId() { return runId; }
    public void setRunId(String value) { runId = value; }
    public String getNodeId() { return nodeId; }
    public void setNodeId(String value) { nodeId = value; }
    public String getNodeType() { return nodeType; }
    public void setNodeType(String value) { nodeType = value; }
    public String getTitle() { return title; }
    public void setTitle(String value) { title = value; }
    public String getInstructions() { return instructions; }
    public void setInstructions(String value) { instructions = value; }
    public String getAssigneeRolesJson() { return assigneeRolesJson; }
    public void setAssigneeRolesJson(String value) { assigneeRolesJson = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public String getDecision() { return decision; }
    public void setDecision(String value) { decision = value; }
    public String getDecisionReason() { return decisionReason; }
    public void setDecisionReason(String value) { decisionReason = value; }
    public String getDecidedBy() { return decidedBy; }
    public void setDecidedBy(String value) { decidedBy = value; }
    public String getCheckpointJson() { return checkpointJson; }
    public void setCheckpointJson(String value) { checkpointJson = value; }
    public String getTimeoutAction() { return timeoutAction; }
    public void setTimeoutAction(String value) { timeoutAction = value; }
    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant value) { expiresAt = value; }
    public Instant getDecidedAt() { return decidedAt; }
    public void setDecidedAt(Instant value) { decidedAt = value; }
    public boolean isTimedOut() { return timedOut; }
    public void setTimedOut(boolean value) { timedOut = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
