package com.agentpowflow.controlplane.integrations;
import org.springframework.data.jpa.repository.JpaRepository; import java.util.*;
public interface JavaScriptToolRepository extends JpaRepository<JavaScriptToolEntity,String>{List<JavaScriptToolEntity> findByTenantIdAndCreatedByOrderByUpdatedAtDesc(String t,String a);Optional<JavaScriptToolEntity> findByToolIdAndTenantIdAndCreatedBy(String id,String t,String a);}
