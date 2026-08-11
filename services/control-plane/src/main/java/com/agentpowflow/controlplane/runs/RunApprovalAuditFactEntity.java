package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "approval_audit_facts")
public class RunApprovalAuditFactEntity {
    @Id @Column(name = "approval_id", nullable = false, length = 64)
    private String approvalId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "node_id", nullable = false, length = 128)
    private String nodeId;
    @Column(name = "assignee_roles_json", nullable = false, columnDefinition = "text")
    private String assigneeRolesJson;
    @Column(name = "status", nullable = false, length = 32)
    private String status;
    @Column(name = "decision", nullable = false, length = 16)
    private String decision = "";
    @Column(name = "timed_out", nullable = false)
    private boolean timedOut;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "terminal_at")
    private Instant terminalAt;
    @Column(name = "decision_duration_seconds")
    private Long decisionDurationSeconds;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist void prePersist() {
        if (createdAt == null) createdAt = Instant.now();
        updatedAt = Instant.now();
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
    public String getAssigneeRolesJson() { return assigneeRolesJson; }
    public void setAssigneeRolesJson(String value) { assigneeRolesJson = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public String getDecision() { return decision; }
    public void setDecision(String value) { decision = value; }
    public boolean isTimedOut() { return timedOut; }
    public void setTimedOut(boolean value) { timedOut = value; }
    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant value) { createdAt = value; }
    public Instant getTerminalAt() { return terminalAt; }
    public void setTerminalAt(Instant value) { terminalAt = value; }
    public Long getDecisionDurationSeconds() { return decisionDurationSeconds; }
    public void setDecisionDurationSeconds(Long value) { decisionDurationSeconds = value; }
    public Instant getUpdatedAt() { return updatedAt; }
}
