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
@Table(name = "run_events", uniqueConstraints = {
        @UniqueConstraint(name = "uk_run_event_external", columnNames = {"tenant_id", "external_event_id"}),
        @UniqueConstraint(name = "uk_run_event_sequence", columnNames = {"run_id", "sequence_no"})
})
public class RunEventEntity {
    @Id @Column(name = "event_id", nullable = false, length = 64)
    private String eventId;
    @Column(name = "external_event_id", nullable = false, length = 128)
    private String externalEventId;
    @Column(name = "tenant_id", nullable = false, length = 64)
    private String tenantId;
    @Column(name = "run_id", nullable = false, length = 64)
    private String runId;
    @Column(name = "sequence_no", nullable = false)
    private long sequenceNo;
    @Column(name = "event_type", nullable = false, length = 64)
    private String eventType;
    @Column(name = "status", nullable = false, length = 32)
    private String status;
    @Column(name = "stage", nullable = false, length = 64)
    private String stage;
    @Column(name = "progress", nullable = false)
    private int progress;
    @Lob @Column(name = "payload_json", nullable = false, columnDefinition = "text")
    private String payloadJson = "{}";
    @Column(name = "source_service", nullable = false, length = 128)
    private String sourceService;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @PrePersist void prePersist() {
        if (eventId == null || eventId.isBlank()) eventId = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = Instant.now();
    }
    public String getEventId() { return eventId; }
    public String getExternalEventId() { return externalEventId; }
    public void setExternalEventId(String v) { externalEventId = v; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String v) { tenantId = v; }
    public String getRunId() { return runId; }
    public void setRunId(String v) { runId = v; }
    public long getSequenceNo() { return sequenceNo; }
    public void setSequenceNo(long v) { sequenceNo = v; }
    public String getEventType() { return eventType; }
    public void setEventType(String v) { eventType = v; }
    public String getStatus() { return status; }
    public void setStatus(String v) { status = v; }
    public String getStage() { return stage; }
    public void setStage(String v) { stage = v; }
    public int getProgress() { return progress; }
    public void setProgress(int v) { progress = v; }
    public String getPayloadJson() { return payloadJson; }
    public void setPayloadJson(String v) { payloadJson = v; }
    public String getSourceService() { return sourceService; }
    public void setSourceService(String v) { sourceService = v; }
    public Instant getCreatedAt() { return createdAt; }
}
