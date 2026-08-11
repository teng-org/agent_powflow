package com.agentpowflow.controlplane.integrations;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.*;
public interface McpConnectionRepository extends JpaRepository<McpConnectionEntity,String>{List<McpConnectionEntity> findByTenantIdAndCreatedByOrderByUpdatedAtDesc(String tenantId,String createdBy);Optional<McpConnectionEntity> findByConnectionIdAndTenantIdAndCreatedBy(String id,String tenantId,String createdBy);Optional<McpConnectionEntity> findByConnectionIdAndTenantId(String id,String tenantId);}
