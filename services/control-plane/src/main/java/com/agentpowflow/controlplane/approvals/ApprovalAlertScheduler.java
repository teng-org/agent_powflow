package com.agentpowflow.controlplane.approvals;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.net.InetAddress;
import java.util.UUID;

@Service
public class ApprovalAlertScheduler {
    private static final Logger log = LoggerFactory.getLogger(ApprovalAlertScheduler.class);
    private final ApprovalAlertService service;
    private final ApprovalAlertWebhookClient webhookClient;
    private final ApprovalAlertProperties properties;
    private final String dispatcherId = dispatcherId();

    public ApprovalAlertScheduler(
            ApprovalAlertService service,
            ApprovalAlertWebhookClient webhookClient,
            ApprovalAlertProperties properties
    ) {
        this.service = service;
        this.webhookClient = webhookClient;
        this.properties = properties;
    }

    @Scheduled(fixedDelayString = "${control-plane.approval-alerts.evaluation-interval-ms:15000}")
    public void evaluate() {
        if (!properties.isEvaluationEnabled()) return;
        try { service.evaluate(); }
        catch (RuntimeException ex) { log.warn("Approval alert evaluation failed reason={}", ex.getMessage()); }
    }

    @Scheduled(fixedDelayString = "${control-plane.approval-alerts.delivery-interval-ms:1000}")
    public void dispatch() {
        if (!properties.isDeliveryEnabled()) return;
        for (ApprovalAlertDeliveryEntity item : service.claimDeliveries(dispatcherId)) {
            try {
                webhookClient.send(item);
                service.markPublished(item.getDeliveryId(), dispatcherId);
            } catch (ApprovalAlertWebhookClient.DeliveryException ex) {
                service.markFailure(item.getDeliveryId(), dispatcherId, ex, ex.isRetryable());
                log.warn("Approval alert delivery failed deliveryId={} retryable={} reason={}",
                        item.getDeliveryId(), ex.isRetryable(), ex.getMessage());
            } catch (RuntimeException ex) {
                service.markFailure(item.getDeliveryId(), dispatcherId, ex, true);
                log.warn("Approval alert delivery failed deliveryId={} reason={}", item.getDeliveryId(), ex.getMessage());
            }
        }
    }

    private static String dispatcherId() {
        try { return InetAddress.getLocalHost().getHostName() + ":approval-alerts:" + UUID.randomUUID(); }
        catch (Exception ignored) { return "control-plane:approval-alerts:" + UUID.randomUUID(); }
    }
}
