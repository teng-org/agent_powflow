package com.agentpowflow.controlplane.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;
import java.util.UUID;

@Component
public class SystemContextFilter extends OncePerRequestFilter {
    public static final String HEADER_TENANT_ID = "X-Tenant-Id";
    public static final String HEADER_ACTOR_ID = "X-Actor-Id";
    public static final String HEADER_TRACE_ID = "X-Trace-Id";
    public static final String HEADER_SOURCE_SERVICE = "X-Source-Service";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String tenantId = attrOrHeaderOrDefault(request, "auth.tenant_id", HEADER_TENANT_ID, "dev-tenant");
        String actorId = attrOrHeaderOrDefault(request, "auth.actor_id", HEADER_ACTOR_ID, "system");
        String traceId = headerOrDefault(request, HEADER_TRACE_ID, UUID.randomUUID().toString());
        String sourceService = headerOrDefault(request, HEADER_SOURCE_SERVICE, "control-plane");

        response.setHeader(HEADER_TRACE_ID, traceId);

        try {
            SystemContextHolder.set(new SystemContext(tenantId, actorId, traceId, sourceService));
            filterChain.doFilter(request, response);
        } finally {
            SystemContextHolder.clear();
        }
    }

    private String headerOrDefault(HttpServletRequest request, String name, String fallback) {
        String value = request.getHeader(name);
        if (value == null || value.isBlank()) {
            return fallback;
        }
        return value;
    }

    private String attrOrHeaderOrDefault(HttpServletRequest request, String attrName, String headerName, String fallback) {
        Object attr = request.getAttribute(attrName);
        if (attr instanceof String s && !s.isBlank()) {
            return s;
        }
        if (attr instanceof List<?> list && !list.isEmpty()) {
            return String.valueOf(list.getFirst());
        }
        return headerOrDefault(request, headerName, fallback);
    }
}
