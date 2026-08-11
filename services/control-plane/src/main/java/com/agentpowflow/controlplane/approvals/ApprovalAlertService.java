package com.agentpowflow.controlplane.approvals;

import com.agentpowflow.controlplane.runs.RunApprovalEntity;
import com.agentpowflow.controlplane.runs.RunApprovalRepository;
import com.agentpowflow.controlplane.web.dto.ApprovalAlertDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

@Service
public class ApprovalAlertService {
    private final ApprovalAlertRepository alertRepository;
    private final ApprovalAlertDeliveryRepository deliveryRepository;
    private final RunApprovalRepository approvalRepository;
    private final ApprovalAlertProperties properties;
    private final ObjectMapper objectMapper;

    public ApprovalAlertService(
            ApprovalAlertRepository alertRepository,
            ApprovalAlertDeliveryRepository deliveryRepository,
            RunApprovalRepository approvalRepository,
            ApprovalAlertProperties properties,
            ObjectMapper objectMapper
    ) {
        this.alertRepository = alertRepository;
        this.deliveryRepository = deliveryRepository;
        this.approvalRepository = approvalRepository;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public ApprovalAlertDto.EvaluationView evaluate() {
        Instant now = Instant.now();
        long dueSoonSeconds = Math.max(60, Math.min(properties.getDueSoonSeconds(), 86_400));
        List<RunApprovalEntity> candidates = approvalRepository
                .findTop100ByStatusAndExpiresAtLessThanEqualOrderByExpiresAtAsc(
                        "PENDING", now.plusSeconds(dueSoonSeconds)
                );
        int created = 0;
        for (RunApprovalEntity approval : candidates) {
            if (!approval.getExpiresAt().isAfter(now)) {
                alertRepository.resolveDueSoonAlert(approval.getApprovalId(), now);
                if (openAlert(approval, "OVERDUE", "CRITICAL", now)) created++;
            } else if (openAlert(approval, "DUE_SOON", "WARNING", now)) {
                created++;
            }
        }
        return new ApprovalAlertDto.EvaluationView(candidates.size(), created, now);
    }

    @Transactional
    public void onApprovalTerminal(RunApprovalEntity approval, boolean timedOut, Instant now) {
        alertRepository.resolveQueueRiskAlerts(approval.getApprovalId(), now);
        if (timedOut) openAlert(approval, "TIMED_OUT", "CRITICAL", now);
    }

    @Transactional(readOnly = true)
    public List<ApprovalAlertDto.AlertView> list(String tenantId, String status) {
        String normalized = status == null ? "" : status.trim().toUpperCase(Locale.ROOT);
        if (!normalized.isEmpty() && !Set.of("OPEN", "ACKNOWLEDGED", "RESOLVED").contains(normalized)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid approval alert status");
        }
        List<ApprovalAlertEntity> values = normalized.isEmpty()
                ? alertRepository.findTop100ByTenantIdOrderByOpenedAtDesc(tenantId)
                : alertRepository.findTop100ByTenantIdAndStatusOrderByOpenedAtDesc(tenantId, normalized);
        return values.stream().map(this::view).toList();
    }

    @Transactional(readOnly = true)
    public ApprovalAlertDto.StatsView stats(String tenantId) {
        Instant now = Instant.now();
        ApprovalAlertRepository.ApprovalAlertStatsProjection value = alertRepository.summarize(
                tenantId, now.minus(24, ChronoUnit.HOURS)
        );
        long deadLetters = deliveryRepository.countDeadLetters(tenantId);
        String health = value.getCriticalCount() > 0 || deadLetters > 0
                ? "CRITICAL" : value.getOpenCount() > 0 ? "WARN" : "HEALTHY";
        return new ApprovalAlertDto.StatsView(
                health, value.getOpenCount(), value.getCriticalCount(), value.getAcknowledgedCount(),
                value.getResolvedCount24h(), value.getTimedOutCount24h(), deadLetters, now
        );
    }

    @Transactional(readOnly = true)
    public ApprovalAlertDto.ConfigView config() {
        String host = destinationHost();
        return new ApprovalAlertDto.ConfigView(
                properties.isEvaluationEnabled(),
                Math.max(60, Math.min(properties.getDueSoonSeconds(), 86_400)),
                !properties.getWebhookUrl().isBlank(), host,
                properties.isAllowHttp(), properties.isAllowPrivateNetwork(),
                Math.max(1, properties.getMaxAttempts()),
                Math.max(500, properties.getRequestTimeoutMs())
        );
    }

    @Transactional
    public ApprovalAlertDto.AlertView acknowledge(String tenantId, String actorId, String alertId) {
        ApprovalAlertEntity item = alertRepository.lockByAlertId(alertId)
                .filter(candidate -> tenantId.equals(candidate.getTenantId()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval alert not found"));
        if ("RESOLVED".equals(item.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "resolved approval alert cannot be acknowledged");
        }
        if (!"ACKNOWLEDGED".equals(item.getStatus())) {
            item.setStatus("ACKNOWLEDGED");
            item.setAcknowledgedAt(Instant.now());
            item.setAcknowledgedBy(actorId);
            alertRepository.saveAndFlush(item);
        }
        return view(item);
    }

    @Transactional
    public ApprovalAlertDto.AlertView replay(String tenantId, String alertId) {
        ApprovalAlertEntity alert = alertRepository.findByAlertIdAndTenantId(alertId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval alert not found"));
        ApprovalAlertDeliveryEntity delivery = deliveryRepository.findByAlertId(alertId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval alert delivery not found"));
        if (!Set.of("DEAD_LETTER", "DISABLED").contains(delivery.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "only disabled or dead-letter deliveries can be replayed");
        }
        if (properties.getWebhookUrl().isBlank()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "approval alert webhook is not configured");
        }
        Instant now = Instant.now();
        delivery.setStatus("PENDING");
        delivery.setAttemptCount(0);
        delivery.setAvailableAt(now);
        delivery.setDestinationHost(destinationHost());
        delivery.setLastError("");
        delivery.setDeadLetteredAt(null);
        delivery.setPublishedAt(null);
        clearLock(delivery);
        deliveryRepository.saveAndFlush(delivery);
        return view(alert);
    }

    @Transactional
    public List<ApprovalAlertDeliveryEntity> claimDeliveries(String dispatcherId) {
        Instant now = Instant.now();
        List<ApprovalAlertDeliveryEntity> items = deliveryRepository.lockDispatchable(
                now, Math.max(1, Math.min(properties.getDeliveryBatchSize(), 100))
        );
        Instant lockedUntil = now.plusSeconds(Math.max(5, properties.getClaimSeconds()));
        for (ApprovalAlertDeliveryEntity item : items) {
            item.setStatus("IN_FLIGHT");
            item.setAttemptCount(item.getAttemptCount() + 1);
            item.setLockedBy(dispatcherId);
            item.setLockedUntil(lockedUntil);
        }
        return deliveryRepository.saveAllAndFlush(items);
    }

    @Transactional
    public void markPublished(String deliveryId, String dispatcherId) {
        ApprovalAlertDeliveryEntity item = ownedDelivery(deliveryId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setStatus("PUBLISHED");
        item.setPublishedAt(now);
        item.setAvailableAt(now);
        item.setLastError("");
        clearLock(item);
        deliveryRepository.save(item);
    }

    @Transactional
    public void markFailure(String deliveryId, String dispatcherId, Throwable error, boolean retryable) {
        ApprovalAlertDeliveryEntity item = ownedDelivery(deliveryId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setLastError(truncate(error == null ? "approval alert delivery failed" : error.getMessage(), 2000));
        clearLock(item);
        if (!retryable || item.getAttemptCount() >= item.getMaxAttempts()) {
            item.setStatus("DEAD_LETTER");
            item.setDeadLetteredAt(now);
            item.setAvailableAt(now);
        } else {
            item.setStatus("RETRY");
            item.setAvailableAt(now.plusSeconds(backoffSeconds(item.getAttemptCount())));
        }
        deliveryRepository.save(item);
    }

    private boolean openAlert(
            RunApprovalEntity approval, String alertType, String severity, Instant now
    ) {
        String alertId = UUID.randomUUID().toString();
        String dedupeKey = "approval:" + approval.getApprovalId() + ":" + alertType.toLowerCase(Locale.ROOT);
        String message = switch (alertType) {
            case "DUE_SOON" -> "Approval is approaching its deadline";
            case "OVERDUE" -> "Approval is overdue";
            case "TIMED_OUT" -> "Approval timed out and used its configured fallback";
            default -> "Approval requires attention";
        };
        int inserted = alertRepository.insertIgnore(
                alertId, dedupeKey, approval.getTenantId(), approval.getApprovalId(), approval.getRunId(),
                approval.getNodeId(), alertType, severity, message, approval.getAssigneeRolesJson(), now
        );
        if (inserted == 0) return false;

        ApprovalAlertDeliveryEntity delivery = new ApprovalAlertDeliveryEntity();
        delivery.setAlertId(alertId);
        delivery.setTenantId(approval.getTenantId());
        delivery.setDestinationHost(destinationHost());
        delivery.setPayloadJson(payload(alertId, approval, alertType, severity, message, now));
        delivery.setStatus(properties.getWebhookUrl().isBlank() ? "DISABLED" : "PENDING");
        delivery.setMaxAttempts(Math.max(1, properties.getMaxAttempts()));
        deliveryRepository.save(delivery);
        return true;
    }

    private String payload(
            String alertId, RunApprovalEntity approval, String alertType,
            String severity, String message, Instant now
    ) {
        var root = objectMapper.createObjectNode()
                .put("schema_version", 1)
                .put("event_id", alertId)
                .put("event_type", "approval.alert.opened")
                .put("alert_type", alertType)
                .put("severity", severity)
                .put("tenant_id", approval.getTenantId())
                .put("approval_id", approval.getApprovalId())
                .put("run_id", approval.getRunId())
                .put("node_id", approval.getNodeId())
                .put("message", message)
                .put("opened_at", now.toString());
        root.set("assignee_roles", readJson(approval.getAssigneeRolesJson()));
        try { return objectMapper.writeValueAsString(root); }
        catch (JsonProcessingException ex) { throw new IllegalStateException("failed to serialize approval alert", ex); }
    }

    private ApprovalAlertDto.AlertView view(ApprovalAlertEntity item) {
        ApprovalAlertDeliveryEntity delivery = deliveryRepository.findByAlertId(item.getAlertId()).orElse(null);
        return new ApprovalAlertDto.AlertView(
                item.getAlertId(), item.getTenantId(), item.getApprovalId(), item.getRunId(), item.getNodeId(),
                item.getAlertType(), item.getSeverity(), item.getMessage(), readRoles(item.getAssigneeRolesJson()),
                item.getStatus(), item.getOpenedAt(), item.getAcknowledgedAt(), item.getAcknowledgedBy(),
                item.getResolvedAt(), item.getRowVersion(), delivery == null ? null : deliveryView(delivery)
        );
    }

    private ApprovalAlertDto.DeliveryView deliveryView(ApprovalAlertDeliveryEntity item) {
        return new ApprovalAlertDto.DeliveryView(
                item.getDeliveryId(), item.getChannel(), item.getDestinationHost(), item.getStatus(),
                item.getAttemptCount(), item.getMaxAttempts(), item.getAvailableAt(), item.getLastError(),
                item.getPublishedAt(), item.getDeadLetteredAt(), item.getRowVersion()
        );
    }

    private ApprovalAlertDeliveryEntity ownedDelivery(String deliveryId, String dispatcherId) {
        return deliveryRepository.lockByDeliveryId(deliveryId)
                .filter(item -> "IN_FLIGHT".equals(item.getStatus()) && dispatcherId.equals(item.getLockedBy()))
                .orElse(null);
    }

    private void clearLock(ApprovalAlertDeliveryEntity item) {
        item.setLockedBy("");
        item.setLockedUntil(null);
    }

    private long backoffSeconds(int attempt) {
        long base = Math.max(1, properties.getRetryBaseSeconds());
        int exponent = Math.max(0, Math.min(attempt - 1, 30));
        long calculated;
        try { calculated = Math.multiplyExact(base, 1L << exponent); }
        catch (ArithmeticException ex) { calculated = Long.MAX_VALUE; }
        return Math.min(calculated, Math.max(base, properties.getRetryMaxSeconds()));
    }

    private String destinationHost() {
        if (properties.getWebhookUrl().isBlank()) return "";
        try {
            URI value = URI.create(properties.getWebhookUrl());
            return value.getHost() == null ? "invalid" : value.getHost().toLowerCase(Locale.ROOT);
        } catch (IllegalArgumentException ex) {
            return "invalid";
        }
    }

    private JsonNode readJson(String value) {
        try { return objectMapper.readTree(value == null || value.isBlank() ? "[]" : value); }
        catch (JsonProcessingException ex) { return objectMapper.createArrayNode(); }
    }

    private List<String> readRoles(String value) {
        JsonNode roles = readJson(value);
        if (!roles.isArray()) return List.of();
        return java.util.stream.StreamSupport.stream(roles.spliterator(), false).map(JsonNode::asText).toList();
    }

    private String truncate(String value, int max) {
        String safe = value == null ? "" : value;
        return safe.length() <= max ? safe : safe.substring(0, max);
    }
}
