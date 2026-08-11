package com.agentpowflow.controlplane.integrations;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity @Table(name="skill_versions", uniqueConstraints=@UniqueConstraint(name="uk_skill_version", columnNames={"skill_id","version_no"}))
public class SkillVersionEntity {
    @Id @Column(name="version_id", length=64) private String versionId;
    @Column(name="skill_id", nullable=false, length=64) private String skillId;
    @Column(name="tenant_id", nullable=false, length=64) private String tenantId;
    @Column(name="version_no", nullable=false) private int versionNo;
    @Lob @Column(name="definition_json", nullable=false, columnDefinition="text") private String definitionJson;
    @Column(nullable=false, length=64) private String checksum;
    @Column(name="published_by", nullable=false, length=128) private String publishedBy;
    @Column(name="published_at", nullable=false) private Instant publishedAt;
    @PrePersist void create(){if(versionId==null)versionId=UUID.randomUUID().toString(); if(publishedAt==null)publishedAt=Instant.now();}
    public String getSkillId(){return skillId;} public void setSkillId(String v){skillId=v;} public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;}
    public int getVersionNo(){return versionNo;} public void setVersionNo(int v){versionNo=v;} public String getDefinitionJson(){return definitionJson;} public void setDefinitionJson(String v){definitionJson=v;}
    public String getChecksum(){return checksum;} public void setChecksum(String v){checksum=v;} public String getPublishedBy(){return publishedBy;} public void setPublishedBy(String v){publishedBy=v;} public Instant getPublishedAt(){return publishedAt;}
}
