package com.agentpowflow.controlplane.runs;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.internal-api")
public class InternalApiProperties {
    private String token = "change-me-in-production";
    private String chatBaseUrl = "http://localhost:8092";
    private int chatRequestTimeoutMs = 180000;

    public String getToken() { return token; }
    public void setToken(String token) { this.token = token; }
    public String getChatBaseUrl() { return chatBaseUrl; }
    public void setChatBaseUrl(String chatBaseUrl) { this.chatBaseUrl = chatBaseUrl; }
    public int getChatRequestTimeoutMs() { return chatRequestTimeoutMs; }
    public void setChatRequestTimeoutMs(int chatRequestTimeoutMs) { this.chatRequestTimeoutMs = chatRequestTimeoutMs; }
}
