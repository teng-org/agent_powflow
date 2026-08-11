package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "tasks")
public class TaskEntity {
    @Id
    @Column(name = "task_id", nullable = false)
    private String taskId;
    @Column(name = "tenant_id", nullable = false) private String tenantId;
    @Column(name = "kb_id", nullable = false) private String kbId;
    @Column(name = "doc_id", nullable = false) private String docId = "";
    @Column(name = "task_type", nullable = false) private String taskType;
    @Column(nullable = false) private String status;
    @Column(nullable = false) private String stage;
    @Column(nullable = false) private String filename = "";
    @Column(name = "batch_id", nullable = false) private String batchId = "";
    @Column(name = "batch_name", nullable = false) private String batchName = "";
    @Column(name = "batch_is_parent", nullable = false) private boolean batchIsParent;
    @Column(name = "progress_current", nullable = false) private int progressCurrent;
    @Column(name = "progress_total", nullable = false) private int progressTotal;
    @Column(name = "progress_pct", nullable = false) private int progressPct;
    @Column(name = "cancel_requested", nullable = false) private boolean cancelRequested;
    @Column(name = "error_message") private String errorMessage;
    @Column(name = "state_owner", nullable = false) private String stateOwner = "EXECUTION_LEGACY";
    @Column(name = "created_at", nullable = false, updatable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        if (updatedAt == null) updatedAt = now;
    }

    @PreUpdate
    void preUpdate() { updatedAt = Instant.now(); }

    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }
    public String getDocId() { return docId; }
    public void setDocId(String docId) { this.docId = docId; }
    public String getTaskType() { return taskType; }
    public void setTaskType(String taskType) { this.taskType = taskType; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getStage() { return stage; }
    public void setStage(String stage) { this.stage = stage; }
    public String getFilename() { return filename; }
    public void setFilename(String filename) { this.filename = filename; }
    public String getBatchId() { return batchId; }
    public void setBatchId(String batchId) { this.batchId = batchId; }
    public String getBatchName() { return batchName; }
    public void setBatchName(String batchName) { this.batchName = batchName; }
    public boolean isBatchIsParent() { return batchIsParent; }
    public void setBatchIsParent(boolean batchIsParent) { this.batchIsParent = batchIsParent; }
    public int getProgressCurrent() { return progressCurrent; }
    public void setProgressCurrent(int progressCurrent) { this.progressCurrent = progressCurrent; }
    public int getProgressTotal() { return progressTotal; }
    public void setProgressTotal(int progressTotal) { this.progressTotal = progressTotal; }
    public int getProgressPct() { return progressPct; }
    public void setProgressPct(int progressPct) { this.progressPct = progressPct; }
    public boolean isCancelRequested() { return cancelRequested; }
    public void setCancelRequested(boolean cancelRequested) { this.cancelRequested = cancelRequested; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
    public String getStateOwner() { return stateOwner; }
    public void setStateOwner(String stateOwner) { this.stateOwner = stateOwner; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }
}
