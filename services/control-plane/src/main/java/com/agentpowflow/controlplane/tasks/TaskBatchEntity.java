package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

@Entity
@Table(name = "task_batches")
public class TaskBatchEntity {
    @Id @Column(name = "batch_id", nullable = false) private String batchId;
    @Column(name = "tenant_id", nullable = false) private String tenantId;
    @Column(name = "kb_id", nullable = false) private String kbId;
    @Column(nullable = false) private String operation;
    @Column(name = "batch_name", nullable = false) private String batchName = "";
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "params_json", nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> params = new LinkedHashMap<>();
    @Column(name = "parent_task_id", nullable = false) private String parentTaskId = "";
    @Column(name = "total_items", nullable = false) private int totalItems;
    @Column(name = "succeeded_items", nullable = false) private int succeededItems;
    @Column(name = "failed_items", nullable = false) private int failedItems;
    @Column(name = "cancelled_items", nullable = false) private int cancelledItems;
    @Column(name = "running_items", nullable = false) private int runningItems;
    @Column(nullable = false) private String status = "RUNNING";
    @Column(name = "created_by", nullable = false) private String createdBy = "system";
    @Column(name = "created_at", nullable = false, updatable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;

    @PrePersist void prePersist() {
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getBatchId() { return batchId; }
    public void setBatchId(String batchId) { this.batchId = batchId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }
    public String getOperation() { return operation; }
    public void setOperation(String operation) { this.operation = operation; }
    public String getBatchName() { return batchName; }
    public void setBatchName(String batchName) { this.batchName = batchName; }
    public Map<String, Object> getParams() { return params; }
    public void setParams(Map<String, Object> params) { this.params = params; }
    public String getParentTaskId() { return parentTaskId; }
    public void setParentTaskId(String parentTaskId) { this.parentTaskId = parentTaskId; }
    public int getTotalItems() { return totalItems; }
    public void setTotalItems(int totalItems) { this.totalItems = totalItems; }
    public int getSucceededItems() { return succeededItems; }
    public void setSucceededItems(int succeededItems) { this.succeededItems = succeededItems; }
    public int getFailedItems() { return failedItems; }
    public void setFailedItems(int failedItems) { this.failedItems = failedItems; }
    public int getCancelledItems() { return cancelledItems; }
    public void setCancelledItems(int cancelledItems) { this.cancelledItems = cancelledItems; }
    public int getRunningItems() { return runningItems; }
    public void setRunningItems(int runningItems) { this.runningItems = runningItems; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
