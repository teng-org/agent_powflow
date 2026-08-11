package com.agentpowflow.controlplane.tasks;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.task-dispatch")
public class TaskDispatchProperties {
    private boolean enabled = true;
    private long intervalMs = 1000;
    private int batchSize = 20;
    private int maxAttempts = 8;
    private long claimSeconds = 30;
    private long retryBaseSeconds = 1;
    private long retryMaxSeconds = 300;
    private int maxTextBytes = 10 * 1024 * 1024;
    private int maxUploadFiles = 20;
    private long maxUploadFileBytes = 50L * 1024 * 1024;
    private long maxUploadBatchBytes = 200L * 1024 * 1024;
    private long uploadIntentTimeoutSeconds = 600;

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
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
    public int getMaxTextBytes() { return maxTextBytes; }
    public void setMaxTextBytes(int maxTextBytes) { this.maxTextBytes = maxTextBytes; }
    public int getMaxUploadFiles() { return maxUploadFiles; }
    public void setMaxUploadFiles(int maxUploadFiles) { this.maxUploadFiles = maxUploadFiles; }
    public long getMaxUploadFileBytes() { return maxUploadFileBytes; }
    public void setMaxUploadFileBytes(long maxUploadFileBytes) { this.maxUploadFileBytes = maxUploadFileBytes; }
    public long getMaxUploadBatchBytes() { return maxUploadBatchBytes; }
    public void setMaxUploadBatchBytes(long maxUploadBatchBytes) { this.maxUploadBatchBytes = maxUploadBatchBytes; }
    public long getUploadIntentTimeoutSeconds() { return uploadIntentTimeoutSeconds; }
    public void setUploadIntentTimeoutSeconds(long uploadIntentTimeoutSeconds) { this.uploadIntentTimeoutSeconds = uploadIntentTimeoutSeconds; }
}
