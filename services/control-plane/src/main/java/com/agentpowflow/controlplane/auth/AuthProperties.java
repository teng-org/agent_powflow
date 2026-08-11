package com.agentpowflow.controlplane.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.auth")
public class AuthProperties {
    private String adminUsername = "admin";
    private String adminPassword = "admin123";
    private String adminRole = "PLATFORM_ADMIN";
    private String adminTenantId = "platform";
    private String jwtSecret = "change-me-in-production";
    private String jwtKeyId = "dev-primary";
    private String jwtPreviousKeys = "";
    private String jwtPreviousKeysExpiresAt = "";
    private String jwtLegacyAcceptUntil = "";
    private String jwtIssuer = "agentpowflow-control";
    private String jwtAudience = "control-plane";
    private long accessTtlSeconds = 43200L;
    private long refreshTtlSeconds = 604800L;

    public String getAdminUsername() {
        return adminUsername;
    }

    public void setAdminUsername(String adminUsername) {
        this.adminUsername = adminUsername;
    }

    public String getAdminPassword() {
        return adminPassword;
    }

    public void setAdminPassword(String adminPassword) {
        this.adminPassword = adminPassword;
    }

    public String getAdminRole() {
        return adminRole;
    }

    public void setAdminRole(String adminRole) {
        this.adminRole = adminRole;
    }

    public String getAdminTenantId() {
        return adminTenantId;
    }

    public void setAdminTenantId(String adminTenantId) {
        this.adminTenantId = adminTenantId;
    }

    public String getJwtSecret() {
        return jwtSecret;
    }

    public void setJwtSecret(String jwtSecret) {
        this.jwtSecret = jwtSecret;
    }

    public String getJwtKeyId() { return jwtKeyId; }

    public void setJwtKeyId(String jwtKeyId) { this.jwtKeyId = jwtKeyId; }

    public String getJwtPreviousKeys() { return jwtPreviousKeys; }

    public void setJwtPreviousKeys(String jwtPreviousKeys) { this.jwtPreviousKeys = jwtPreviousKeys; }

    public String getJwtPreviousKeysExpiresAt() { return jwtPreviousKeysExpiresAt; }

    public void setJwtPreviousKeysExpiresAt(String jwtPreviousKeysExpiresAt) { this.jwtPreviousKeysExpiresAt = jwtPreviousKeysExpiresAt; }

    public String getJwtLegacyAcceptUntil() { return jwtLegacyAcceptUntil; }

    public void setJwtLegacyAcceptUntil(String jwtLegacyAcceptUntil) { this.jwtLegacyAcceptUntil = jwtLegacyAcceptUntil; }

    public String getJwtIssuer() { return jwtIssuer; }

    public void setJwtIssuer(String jwtIssuer) { this.jwtIssuer = jwtIssuer; }

    public String getJwtAudience() { return jwtAudience; }

    public void setJwtAudience(String jwtAudience) { this.jwtAudience = jwtAudience; }

    public long getAccessTtlSeconds() {
        return accessTtlSeconds;
    }

    public void setAccessTtlSeconds(long accessTtlSeconds) {
        this.accessTtlSeconds = accessTtlSeconds;
    }

    public long getRefreshTtlSeconds() {
        return refreshTtlSeconds;
    }

    public void setRefreshTtlSeconds(long refreshTtlSeconds) {
        this.refreshTtlSeconds = refreshTtlSeconds;
    }
}
