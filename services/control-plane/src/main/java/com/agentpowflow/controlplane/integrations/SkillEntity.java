package com.agentpowflow.controlplane.integrations;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity @Table(name = "skills")
public class SkillEntity {
    @Id @Column(name="skill_id", length=64) private String skillId;
    @Column(name="tenant_id", nullable=false, length=64) private String tenantId;
    @Column(nullable=false, length=128) private String name;
    @Column(nullable=false, length=1000) private String description = "";
    @Column(nullable=false, length=32) private String status = "DRAFT";
    @Column(name="latest_version_no", nullable=false) private int latestVersionNo;
    @Lob @Column(name="draft_definition_json", nullable=false, columnDefinition="text") private String draftDefinitionJson = "{}";
    @Column(name="created_by", nullable=false, length=128) private String createdBy;
    @Column(name="updated_by", nullable=false, length=128) private String updatedBy;
    @Column(name="created_at", nullable=false) private Instant createdAt;
    @Column(name="updated_at", nullable=false) private Instant updatedAt;
    @Version @Column(name="row_version", nullable=false) private long rowVersion;
    @PrePersist void create(){ if(skillId==null) skillId=UUID.randomUUID().toString(); var now=Instant.now(); createdAt=now; updatedAt=now; }
    @PreUpdate void update(){ updatedAt=Instant.now(); }
    public String getSkillId(){return skillId;} public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;}
    public String getName(){return name;} public void setName(String v){name=v;} public String getDescription(){return description;} public void setDescription(String v){description=v;}
    public String getStatus(){return status;} public void setStatus(String v){status=v;} public int getLatestVersionNo(){return latestVersionNo;} public void setLatestVersionNo(int v){latestVersionNo=v;}
    public String getDraftDefinitionJson(){return draftDefinitionJson;} public void setDraftDefinitionJson(String v){draftDefinitionJson=v;}
    public String getCreatedBy(){return createdBy;} public void setCreatedBy(String v){createdBy=v;} public String getUpdatedBy(){return updatedBy;} public void setUpdatedBy(String v){updatedBy=v;}
    public Instant getCreatedAt(){return createdAt;} public Instant getUpdatedAt(){return updatedAt;} public long getRowVersion(){return rowVersion;}
}
