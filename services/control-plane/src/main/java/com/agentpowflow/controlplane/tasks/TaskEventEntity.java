package com.agentpowflow.controlplane.tasks;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@Entity
@Table(name = "task_events")
public class TaskEventEntity {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "event_id")
    private Long eventId;
    @Column(name = "task_id", nullable = false) private String taskId;
    @Column(name = "tenant_id", nullable = false) private String tenantId;
    @Column(name = "kb_id", nullable = false) private String kbId;
    @Column(name = "doc_id", nullable = false) private String docId = "";
    @Column(name = "event_type", nullable = false) private String eventType;
    @Column(name = "event_key", nullable = false) private String eventKey = "";
    @Column(nullable = false) private String stage;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> payload = new HashMap<>();
    @Column(name = "ts", nullable = false, updatable = false) private Instant timestamp;

    @PrePersist
    void initializeTimestamp() {
        if (timestamp == null) timestamp = Instant.now();
    }

    public Long getEventId() { return eventId; }
    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }
    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }
    public String getKbId() { return kbId; }
    public void setKbId(String kbId) { this.kbId = kbId; }
    public String getDocId() { return docId; }
    public void setDocId(String docId) { this.docId = docId; }
    public String getEventType() { return eventType; }
    public void setEventType(String eventType) { this.eventType = eventType; }
    public String getEventKey() { return eventKey; }
    public void setEventKey(String eventKey) { this.eventKey = eventKey; }
    public String getStage() { return stage; }
    public void setStage(String stage) { this.stage = stage; }
    public Map<String, Object> getPayload() { return payload; }
    public void setPayload(Map<String, Object> payload) { this.payload = payload; }
    public Instant getTimestamp() { return timestamp; }
}
