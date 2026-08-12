package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "run_node_executions", uniqueConstraints = {
        @UniqueConstraint(name = "uk_run_node_execution", columnNames = {"tenant_id", "run_id", "node_id"})
})
public class RunNodeExecutionEntity {
    @Id @Column(name = "node_execution_id", nullable = false, length = 64)
    private String nodeExecutionId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "node_id", nullable = false, length = 128)
    private String nodeId;
    @Column(name = "node_type", nullable = false, length = 64)
    private String nodeType;
    @Column(name = "ordinal", nullable = false)
    private int ordinal;
    @Column(name = "status", nullable = false, length = 32)
    private String status;
    @Column(name = "started_at")
    private Instant startedAt;
    @Column(name = "completed_at")
    private Instant completedAt;
    @Column(name = "duration_ms")
    private Long durationMs;
    @Column(name = "details_json", nullable = false, columnDefinition = "text")
    private String detailsJson = "{}";
    @Column(name = "error_message", nullable = false, length = 2000)
    private String errorMessage = "";
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist void prePersist() {
        Instant now = Instant.now();
        if (nodeExecutionId == null || nodeExecutionId.isBlank()) nodeExecutionId = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = now;
        if (updatedAt == null) updatedAt = now;
    }

    public String getNodeExecutionId() { return nodeExecutionId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getRunId() { return runId; }
    public void setRunId(String value) { runId = value; }
    public String getNodeId() { return nodeId; }
    public void setNodeId(String value) { nodeId = value; }
    public String getNodeType() { return nodeType; }
    public void setNodeType(String value) { nodeType = value; }
    public int getOrdinal() { return ordinal; }
    public void setOrdinal(int value) { ordinal = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public Instant getStartedAt() { return startedAt; }
    public void setStartedAt(Instant value) { startedAt = value; }
    public Instant getCompletedAt() { return completedAt; }
    public void setCompletedAt(Instant value) { completedAt = value; }
    public Long getDurationMs() { return durationMs; }
    public void setDurationMs(Long value) { durationMs = value; }
    public String getDetailsJson() { return detailsJson; }
    public void setDetailsJson(String value) { detailsJson = value; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String value) { errorMessage = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant value) { updatedAt = value; }
}
