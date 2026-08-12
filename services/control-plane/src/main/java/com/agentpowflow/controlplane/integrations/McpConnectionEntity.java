package com.agentpowflow.controlplane.integrations;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity @Table(name="mcp_connections")
public class McpConnectionEntity {
    @Id @Column(name="connection_id", length=64) private String connectionId;
    @Column(name="tenant_id", nullable=false, length=64) private String tenantId;
    @Column(nullable=false, length=128) private String name;
    @Column(nullable=false, length=1000) private String description="";
    @Column(nullable=false, length=32) private String transport="STREAMABLE_HTTP";
    @Column(name="endpoint_url", nullable=false, length=2000) private String endpointUrl;
    @Column(name="auth_type", nullable=false, length=32) private String authType="NONE";
    @Column(name="secret_ref", length=256) private String secretRef;
    @Column(nullable=false, length=32) private String status="DRAFT";
    @Column(name="tools_json", nullable=false, columnDefinition="text") private String toolsJson="[]";
    @Column(name="created_by", nullable=false, length=128) private String createdBy;
    @Column(name="updated_by", nullable=false, length=128) private String updatedBy;
    @Column(name="created_at", nullable=false) private Instant createdAt;
    @Column(name="updated_at", nullable=false) private Instant updatedAt;
    @Version @Column(name="row_version", nullable=false) private long rowVersion;
    @PrePersist void create(){if(connectionId==null)connectionId=UUID.randomUUID().toString();var now=Instant.now();createdAt=now;updatedAt=now;} @PreUpdate void update(){updatedAt=Instant.now();}
    public String getConnectionId(){return connectionId;} public String getTenantId(){return tenantId;} public void setTenantId(String v){tenantId=v;} public String getName(){return name;} public void setName(String v){name=v;}
    public String getDescription(){return description;} public void setDescription(String v){description=v;} public String getTransport(){return transport;} public void setTransport(String v){transport=v;}
    public String getEndpointUrl(){return endpointUrl;} public void setEndpointUrl(String v){endpointUrl=v;} public String getAuthType(){return authType;} public void setAuthType(String v){authType=v;} public String getSecretRef(){return secretRef;} public void setSecretRef(String v){secretRef=v;}
    public String getStatus(){return status;} public void setStatus(String v){status=v;} public String getToolsJson(){return toolsJson;} public void setToolsJson(String v){toolsJson=v;}
    public String getCreatedBy(){return createdBy;} public void setCreatedBy(String v){createdBy=v;} public String getUpdatedBy(){return updatedBy;} public void setUpdatedBy(String v){updatedBy=v;} public Instant getCreatedAt(){return createdAt;} public Instant getUpdatedAt(){return updatedAt;} public long getRowVersion(){return rowVersion;}
}
