package com.agentpowflow.controlplane.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "deployment_invocations")
public class DeploymentInvocationEntity {
    @Id
    @Column(name = "invocation_id", nullable = false, length = 64)
    private String invocationId;
    @Column(name = "deployment_id", nullable = false, length = 64)
    private String deploymentId;
    @Column(name = "api_key_id", nullable = false, length = 64)
    private String apiKeyId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "run_id", length = 64)
    private String runId;
    @Column(name = "status", nullable = false, length = 32)
    private String status;
    @Column(name = "input_bytes", nullable = false)
    private long inputBytes;
    @Column(name = "output_bytes", nullable = false)
    private long outputBytes;
    @Column(name = "duration_ms")
    private Long durationMs;
    @Column(name = "error_code", nullable = false, length = 128)
    private String errorCode = "";
    @Column(name = "trace_id", nullable = false, length = 128)
    private String traceId;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @PrePersist
    void prePersist() {
        if (invocationId == null || invocationId.isBlank()) invocationId = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = Instant.now();
    }

    public String getInvocationId() { return invocationId; }
    public String getDeploymentId() { return deploymentId; }
    public void setDeploymentId(String deploymentId) { this.deploymentId = deploymentId; }
    public String getApiKeyId() { return apiKeyId; }
    public void setApiKeyId(String apiKeyId) { this.apiKeyId = apiKeyId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getRunId() { return runId; }
    public void setRunId(String runId) { this.runId = runId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public long getInputBytes() { return inputBytes; }
    public void setInputBytes(long inputBytes) { this.inputBytes = inputBytes; }
    public long getOutputBytes() { return outputBytes; }
    public void setOutputBytes(long outputBytes) { this.outputBytes = outputBytes; }
    public Long getDurationMs() { return durationMs; }
    public void setDurationMs(Long durationMs) { this.durationMs = durationMs; }
    public String getErrorCode() { return errorCode; }
    public void setErrorCode(String errorCode) { this.errorCode = errorCode; }
    public String getTraceId() { return traceId; }
    public void setTraceId(String traceId) { this.traceId = traceId; }
    public Instant getCreatedAt() { return createdAt; }
}
