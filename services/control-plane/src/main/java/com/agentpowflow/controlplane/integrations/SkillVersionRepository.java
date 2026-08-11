package com.agentpowflow.controlplane.integrations;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.*;
public interface SkillVersionRepository extends JpaRepository<SkillVersionEntity,String>{List<SkillVersionEntity> findBySkillIdAndTenantIdOrderByVersionNoDesc(String id,String tenantId);Optional<SkillVersionEntity> findBySkillIdAndTenantIdAndVersionNo(String id,String tenantId,int version);}
