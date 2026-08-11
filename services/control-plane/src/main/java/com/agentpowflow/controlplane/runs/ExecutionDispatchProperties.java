package com.agentpowflow.controlplane.runs;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.execution-dispatch")
public class ExecutionDispatchProperties {
    private boolean enabled = true;
    private String url = "http://localhost:8090/api/v1/internal/control-runs/dispatch";
    private long intervalMs = 1000;
    private int batchSize = 20;
    private int maxAttempts = 8;
    private long claimSeconds = 30;
    private long retryBaseSeconds = 1;
    private long retryMaxSeconds = 300;

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public String getUrl() { return url; }
    public void setUrl(String url) { this.url = url; }
    public long getIntervalMs() { return intervalMs; }
    public void setIntervalMs(long intervalMs) { this.intervalMs = intervalMs; }
    public int getBatchSize() { return batchSize; }
    public void setBatchSize(int batchSize) { this.batchSize = batchSize; }
    public int getMaxAttempts() { return maxAttempts; }
    public void setMaxAttempts(int maxAttempts) { this.maxAttempts = maxAttempts; }
    public long getClaimSeconds() { return claimSeconds; }
    public void setClaimSeconds(long claimSeconds) { this.claimSeconds = claimSeconds; }
    public long getRetryBaseSeconds() { return retryBaseSeconds; }
    public void setRetryBaseSeconds(long retryBaseSeconds) { this.retryBaseSeconds = retryBaseSeconds; }
    public long getRetryMaxSeconds() { return retryMaxSeconds; }
    public void setRetryMaxSeconds(long retryMaxSeconds) { this.retryMaxSeconds = retryMaxSeconds; }
}
