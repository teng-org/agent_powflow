package com.agentpowflow.controlplane.integrations;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity @Table(name="javascript_tools")
public class JavaScriptToolEntity {
    @Id @Column(name="tool_id",length=64) private String toolId;
    @Column(name="tenant_id",nullable=false,length=64) private String tenantId;
    @Column(nullable=false,length=128) private String name;
    @Column(nullable=false,length=1000) private String description="";
    @Column(nullable=false,length=32) private String status="DRAFT";
    @Column(name="latest_version_no",nullable=false) private int latestVersionNo;
    @Column(name="draft_definition_json",nullable=false,columnDefinition="text") private String draftDefinitionJson="{}";
    @Column(name="created_by",nullable=false,length=128) private String createdBy;
    @Column(name="updated_by",nullable=false,length=128) private String updatedBy;
    @Column(name="created_at",nullable=false) private Instant createdAt;
    @Column(name="updated_at",nullable=false) private Instant updatedAt;
    @Version @Column(name="row_version",nullable=false) private long rowVersion;
    @PrePersist void create(){if(toolId==null)toolId=UUID.randomUUID().toString();var n=Instant.now();createdAt=n;updatedAt=n;} @PreUpdate void update(){updatedAt=Instant.now();}
    public String getToolId(){return toolId;} public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;} public String getName(){return name;} public void setName(String v){name=v;} public String getDescription(){return description;} public void setDescription(String v){description=v;} public String getStatus(){return status;} public void setStatus(String v){status=v;} public int getLatestVersionNo(){return latestVersionNo;} public void setLatestVersionNo(int v){latestVersionNo=v;} public String getDraftDefinitionJson(){return draftDefinitionJson;} public void setDraftDefinitionJson(String v){draftDefinitionJson=v;} public String getCreatedBy(){return createdBy;} public void setCreatedBy(String v){createdBy=v;} public void setUpdatedBy(String v){updatedBy=v;} public Instant getUpdatedAt(){return updatedAt;} public long getRowVersion(){return rowVersion;}
}
