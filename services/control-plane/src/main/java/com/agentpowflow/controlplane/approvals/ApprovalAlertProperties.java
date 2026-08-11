package com.agentpowflow.controlplane.approvals;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.approval-alerts")
public class ApprovalAlertProperties {
    private boolean evaluationEnabled = true;
    private long evaluationIntervalMs = 15_000;
    private long dueSoonSeconds = 900;
    private String webhookUrl = "";
    private String allowedHosts = "";
    private boolean allowHttp;
    private boolean allowPrivateNetwork;
    private boolean deliveryEnabled = true;
    private long deliveryIntervalMs = 1_000;
    private int deliveryBatchSize = 20;
    private int maxAttempts = 8;
    private long claimSeconds = 30;
    private long retryBaseSeconds = 5;
    private long retryMaxSeconds = 900;
    private long requestTimeoutMs = 5_000;

    public boolean isEvaluationEnabled() { return evaluationEnabled; }
    public void setEvaluationEnabled(boolean value) { evaluationEnabled = value; }
    public long getEvaluationIntervalMs() { return evaluationIntervalMs; }
    public void setEvaluationIntervalMs(long value) { evaluationIntervalMs = value; }
    public long getDueSoonSeconds() { return dueSoonSeconds; }
    public void setDueSoonSeconds(long value) { dueSoonSeconds = value; }
    public String getWebhookUrl() { return webhookUrl; }
    public void setWebhookUrl(String value) { webhookUrl = value == null ? "" : value.trim(); }
    public String getAllowedHosts() { return allowedHosts; }
    public void setAllowedHosts(String value) { allowedHosts = value == null ? "" : value.trim(); }
    public boolean isAllowHttp() { return allowHttp; }
    public void setAllowHttp(boolean value) { allowHttp = value; }
    public boolean isAllowPrivateNetwork() { return allowPrivateNetwork; }
    public void setAllowPrivateNetwork(boolean value) { allowPrivateNetwork = value; }
    public boolean isDeliveryEnabled() { return deliveryEnabled; }
    public void setDeliveryEnabled(boolean value) { deliveryEnabled = value; }
    public long getDeliveryIntervalMs() { return deliveryIntervalMs; }
    public void setDeliveryIntervalMs(long value) { deliveryIntervalMs = value; }
    public int getDeliveryBatchSize() { return deliveryBatchSize; }
    public void setDeliveryBatchSize(int value) { deliveryBatchSize = value; }
    public int getMaxAttempts() { return maxAttempts; }
    public void setMaxAttempts(int value) { maxAttempts = value; }
    public long getClaimSeconds() { return claimSeconds; }
    public void setClaimSeconds(long value) { claimSeconds = value; }
    public long getRetryBaseSeconds() { return retryBaseSeconds; }
    public void setRetryBaseSeconds(long value) { retryBaseSeconds = value; }
    public long getRetryMaxSeconds() { return retryMaxSeconds; }
    public void setRetryMaxSeconds(long value) { retryMaxSeconds = value; }
    public long getRequestTimeoutMs() { return requestTimeoutMs; }
    public void setRequestTimeoutMs(long value) { requestTimeoutMs = value; }
}
