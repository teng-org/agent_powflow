package com.agentpowflow.controlplane.runs;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "run_dead_letters", uniqueConstraints = @UniqueConstraint(name = "uk_run_dead_letters_task", columnNames = "failed_task_id"))
public class RunDeadLetterEntity {
    @Id @Column(name = "dead_letter_id", length = 64) private String deadLetterId;
    @Column(name = "tenant_id", nullable = false, length = 64) private String tenantId;
    @Column(name = "run_id", nullable = false, length = 64) private String runId;
    @Column(name = "failed_task_id", nullable = false, length = 128) private String failedTaskId;
    @Column(name = "source_queue", nullable = false, length = 128) private String sourceQueue;
    @Column(name = "error", nullable = false, length = 2000) private String error = "";
    @Column(name = "payload_json", nullable = false, columnDefinition = "text") private String payloadJson = "{}";
    @Column(name = "status", nullable = false, length = 32) private String status = "OPEN";
    @Column(name = "replay_count", nullable = false) private int replayCount;
    @Column(name = "last_error", nullable = false, length = 2000) private String lastError = "";
    @Column(name = "replay_requested_at") private Instant replayRequestedAt;
    @Column(name = "replayed_at") private Instant replayedAt;
    @Column(name = "replayed_by", nullable = false, length = 128) private String replayedBy = "";
    @Column(name = "created_at", nullable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;
    @Version @Column(name = "row_version", nullable = false) private long rowVersion;

    @PrePersist void prePersist() { if (deadLetterId == null) deadLetterId = UUID.randomUUID().toString(); Instant now=Instant.now(); if(createdAt==null) createdAt=now; updatedAt=now; }
    @PreUpdate void preUpdate() { updatedAt=Instant.now(); }
    public String getDeadLetterId(){return deadLetterId;} public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;}
    public String getRunId(){return runId;} public void setRunId(String v){runId=v;} public String getFailedTaskId(){return failedTaskId;} public void setFailedTaskId(String v){failedTaskId=v;}
    public String getSourceQueue(){return sourceQueue;} public void setSourceQueue(String v){sourceQueue=v;} public String getError(){return error;} public void setError(String v){error=v;}
    public String getPayloadJson(){return payloadJson;} public void setPayloadJson(String v){payloadJson=v;} public String getStatus(){return status;} public void setStatus(String v){status=v;}
    public int getReplayCount(){return replayCount;} public void setReplayCount(int v){replayCount=v;} public String getLastError(){return lastError;} public void setLastError(String v){lastError=v;}
    public Instant getReplayRequestedAt(){return replayRequestedAt;} public void setReplayRequestedAt(Instant v){replayRequestedAt=v;} public Instant getReplayedAt(){return replayedAt;} public void setReplayedAt(Instant v){replayedAt=v;}
    public String getReplayedBy(){return replayedBy;} public void setReplayedBy(String v){replayedBy=v;} public Instant getCreatedAt(){return createdAt;} public Instant getUpdatedAt(){return updatedAt;} public long getRowVersion(){return rowVersion;}
}
