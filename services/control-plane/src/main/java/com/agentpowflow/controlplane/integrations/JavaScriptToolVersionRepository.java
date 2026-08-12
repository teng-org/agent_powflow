package com.agentpowflow.controlplane.integrations;
import org.springframework.data.jpa.repository.JpaRepository; import java.util.*;
public interface JavaScriptToolVersionRepository extends JpaRepository<JavaScriptToolVersionEntity,String>{List<JavaScriptToolVersionEntity> findByToolIdAndTenantIdOrderByVersionNoDesc(String id,String t);Optional<JavaScriptToolVersionEntity> findByToolIdAndTenantIdAndVersionNo(String id,String t,int v);}
