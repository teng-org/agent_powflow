package com.agentpowflow.controlplane.runs;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "run_dead_letter_audit")
public class RunDeadLetterAuditEntity {
    @Id @Column(name="audit_id", length=64) private String auditId;
    @Column(name="dead_letter_id", nullable=false, length=64) private String deadLetterId;
    @Column(name="tenant_id", nullable=false, length=64) private String tenantId;
    @Column(name="run_id", nullable=false, length=64) private String runId;
    @Column(name="action", nullable=false, length=32) private String action;
    @Column(name="actor_id", nullable=false, length=128) private String actorId;
    @Column(name="detail_json", nullable=false, columnDefinition="text") private String detailJson="{}";
    @Column(name="created_at", nullable=false) private Instant createdAt;
    @PrePersist void prePersist(){if(auditId==null)auditId=UUID.randomUUID().toString();if(createdAt==null)createdAt=Instant.now();}
    public String getAuditId(){return auditId;} public String getDeadLetterId(){return deadLetterId;} public void setDeadLetterId(String v){deadLetterId=v;}
    public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;} public String getRunId(){return runId;} public void setRunId(String v){runId=v;}
    public String getAction(){return action;} public void setAction(String v){action=v;} public String getActorId(){return actorId;} public void setActorId(String v){actorId=v;}
    public String getDetailJson(){return detailJson;} public void setDetailJson(String v){detailJson=v;} public Instant getCreatedAt(){return createdAt;}
}
