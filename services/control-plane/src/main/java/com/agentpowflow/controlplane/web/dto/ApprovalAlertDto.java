package com.agentpowflow.controlplane.web.dto;

import java.time.Instant;
import java.util.List;

public final class ApprovalAlertDto {
    private ApprovalAlertDto() {}

    public record AlertView(
            String alert_id, String tenant_id, String approval_id, String run_id, String node_id,
            String alert_type, String severity, String message, List<String> assignee_roles,
            String status, Instant opened_at, Instant acknowledged_at, String acknowledged_by,
            Instant resolved_at, long revision, DeliveryView delivery
    ) {}

    public record DeliveryView(
            String delivery_id, String channel, String destination_host, String status,
            int attempt_count, int max_attempts, Instant available_at, String last_error,
            Instant published_at, Instant dead_lettered_at, long revision
    ) {}

    public record StatsView(
            String health, long open_count, long critical_count, long acknowledged_count,
            long resolved_count_24h, long timed_out_count_24h, long delivery_dead_letter_count,
            Instant generated_at
    ) {}

    public record ConfigView(
            boolean evaluation_enabled, long due_soon_seconds, boolean destination_configured,
            String destination_host, boolean http_allowed, boolean private_network_allowed,
            int max_attempts, long request_timeout_ms
    ) {}

    public record EvaluationView(int examined_count, int created_count, Instant evaluated_at) {}
    public record AlertListResponse(String status, List<AlertView> items) {}
    public record AlertResponse(String status, AlertView item, boolean duplicate) {}
    public record StatsResponse(String status, StatsView item) {}
    public record ConfigResponse(String status, ConfigView item) {}
    public record EvaluationResponse(String status, EvaluationView item) {}
}
