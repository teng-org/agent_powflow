package com.agentpowflow.controlplane.config;

public record SystemContext(String tenantId, String actorId, String traceId, String sourceService) {
}
