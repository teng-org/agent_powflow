package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "runs")
public class RunEntity {
    @Id @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "conversation_id", nullable = false, length = 64)
    private String conversationId;
    @Column(name = "workflow_id", nullable = false, length = 64)
    private String workflowId;
    @Column(name = "workflow_version_id", nullable = false, length = 64)
    private String workflowVersionId;
    @Column(name = "workflow_version_no", nullable = false)
    private int workflowVersionNo;
    @Column(name = "workflow_checksum", nullable = false, length = 64)
    private String workflowChecksum;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "PENDING";
    @Column(name = "stage", nullable = false, length = 64)
    private String stage = "QUEUED";
    @Column(name = "progress", nullable = false)
    private int progress;
    @Lob @Column(name = "input_payload_json", nullable = false, columnDefinition = "text")
    private String inputPayloadJson = "{}";
    @Lob @Column(name = "output_payload_json", nullable = false, columnDefinition = "text")
    private String outputPayloadJson = "{}";
    @Lob @Column(name = "citations_json", nullable = false, columnDefinition = "text")
    private String citationsJson = "[]";
    @Lob @Column(name = "metrics_json", nullable = false, columnDefinition = "text")
    private String metricsJson = "{}";
    @Column(name = "error_code", nullable = false, length = 128)
    private String errorCode = "";
    @Column(name = "error_message", nullable = false, length = 2000)
    private String errorMessage = "";
    @Column(name = "created_by", nullable = false, length = 128)
    private String createdBy;
    @Column(name = "started_at") private Instant startedAt;
    @Column(name = "finished_at") private Instant finishedAt;
    @Column(name = "created_at", nullable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false) private long rowVersion;

    @PrePersist void prePersist() {
        if (runId == null || runId.isBlank()) runId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getRunId() { return runId; }
    public void setRunId(String v) { runId = v; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String v) { tenantId = v; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String v) { conversationId = v; }
    public String getWorkflowId() { return workflowId; }
    public void setWorkflowId(String v) { workflowId = v; }
    public String getWorkflowVersionId() { return workflowVersionId; }
    public void setWorkflowVersionId(String v) { workflowVersionId = v; }
    public int getWorkflowVersionNo() { return workflowVersionNo; }
    public void setWorkflowVersionNo(int v) { workflowVersionNo = v; }
    public String getWorkflowChecksum() { return workflowChecksum; }
    public void setWorkflowChecksum(String v) { workflowChecksum = v; }
    public String getStatus() { return status; }
    public void setStatus(String v) { status = v; }
    public String getStage() { return stage; }
    public void setStage(String v) { stage = v; }
    public int getProgress() { return progress; }
    public void setProgress(int v) { progress = v; }
    public String getInputPayloadJson() { return inputPayloadJson; }
    public void setInputPayloadJson(String v) { inputPayloadJson = v; }
    public String getOutputPayloadJson() { return outputPayloadJson; }
    public void setOutputPayloadJson(String v) { outputPayloadJson = v; }
    public String getCitationsJson() { return citationsJson; }
    public void setCitationsJson(String v) { citationsJson = v; }
    public String getMetricsJson() { return metricsJson; }
    public void setMetricsJson(String v) { metricsJson = v; }
    public String getErrorCode() { return errorCode; }
    public void setErrorCode(String v) { errorCode = v; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String v) { errorMessage = v; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String v) { createdBy = v; }
    public Instant getStartedAt() { return startedAt; }
    public void setStartedAt(Instant v) { startedAt = v; }
    public Instant getFinishedAt() { return finishedAt; }
    public void setFinishedAt(Instant v) { finishedAt = v; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
