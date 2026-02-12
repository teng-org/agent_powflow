package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.config.SystemContext;
import com.agentpowflow.controlplane.config.SystemContextHolder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/system")
public class SystemController {

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> result = new HashMap<>();
        result.put("status", "ok");
        result.put("service", "control-plane");
        return result;
    }

    @GetMapping("/context")
    public Map<String, Object> context() {
        SystemContext context = SystemContextHolder.get();
        Map<String, Object> result = new HashMap<>();
        result.put("tenant_id", context.tenantId());
        result.put("actor_id", context.actorId());
        result.put("trace_id", context.traceId());
        result.put("source_service", context.sourceService());
        return result;
    }
}
