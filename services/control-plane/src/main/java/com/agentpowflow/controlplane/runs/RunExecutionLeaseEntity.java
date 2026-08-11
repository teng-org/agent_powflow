package com.agentpowflow.controlplane.runs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;

@Entity
@Table(name = "run_execution_leases")
public class RunExecutionLeaseEntity {
    @Id @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "lease_token", nullable = false, length = 64)
    private String leaseToken;
    @Column(name = "worker_id", nullable = false, length = 200)
    private String workerId;
    @Column(name = "status", nullable = false, length = 32)
    private String status = "ACTIVE";
    @Column(name = "attempt_no", nullable = false)
    private int attemptNo;
    @Column(name = "acquired_at", nullable = false)
    private Instant acquiredAt;
    @Column(name = "heartbeat_at", nullable = false)
    private Instant heartbeatAt;
    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;
    @Column(name = "released_at")
    private Instant releasedAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false)
    private long rowVersion;

    @PrePersist void prePersist() {
        Instant now = Instant.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }
    @PreUpdate void preUpdate() { updatedAt = Instant.now(); }

    public String getRunId() { return runId; }
    public void setRunId(String value) { runId = value; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String value) { tenantId = value; }
    public String getLeaseToken() { return leaseToken; }
    public void setLeaseToken(String value) { leaseToken = value; }
    public String getWorkerId() { return workerId; }
    public void setWorkerId(String value) { workerId = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public int getAttemptNo() { return attemptNo; }
    public void setAttemptNo(int value) { attemptNo = value; }
    public Instant getAcquiredAt() { return acquiredAt; }
    public void setAcquiredAt(Instant value) { acquiredAt = value; }
    public Instant getHeartbeatAt() { return heartbeatAt; }
    public void setHeartbeatAt(Instant value) { heartbeatAt = value; }
    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant value) { expiresAt = value; }
    public Instant getReleasedAt() { return releasedAt; }
    public void setReleasedAt(Instant value) { releasedAt = value; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getRowVersion() { return rowVersion; }
}
