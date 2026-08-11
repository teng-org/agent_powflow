package com.agentpowflow.controlplane.knowledge;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.knowledge")
public class KnowledgeExecutionProperties {
    private String executionBaseUrl = "http://localhost:8090";
    private int requestTimeoutMs = 10_000;

    public String getExecutionBaseUrl() { return executionBaseUrl; }
    public void setExecutionBaseUrl(String executionBaseUrl) { this.executionBaseUrl = executionBaseUrl; }
    public int getRequestTimeoutMs() { return requestTimeoutMs; }
    public void setRequestTimeoutMs(int requestTimeoutMs) { this.requestTimeoutMs = requestTimeoutMs; }
}
