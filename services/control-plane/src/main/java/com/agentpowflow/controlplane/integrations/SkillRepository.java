package com.agentpowflow.controlplane.integrations;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.*;
public interface SkillRepository extends JpaRepository<SkillEntity,String>{List<SkillEntity> findByTenantIdAndCreatedByOrderByUpdatedAtDesc(String tenantId,String createdBy);Optional<SkillEntity> findBySkillIdAndTenantIdAndCreatedBy(String id,String tenantId,String createdBy);}
