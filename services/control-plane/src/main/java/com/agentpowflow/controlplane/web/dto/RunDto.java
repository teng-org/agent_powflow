package com.agentpowflow.controlplane.web.dto;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class RunDto {
    private RunDto() {
    }

    public record CreateConversationRequest(@Size(max = 160) String title) {
    }

    public record CreateMessageRunRequest(
            @NotNull JsonNode content,
            @NotBlank String workflow_id,
            @Min(1) int workflow_version_no
    ) {
    }

    public record InternalEventRequest(
            @NotBlank String event_id,
            @NotBlank String tenant_id,
            @NotBlank String source_service,
            @NotBlank String event_type,
            @Pattern(regexp = "PENDING|RUNNING|WAITING_APPROVAL|SUCCEEDED|FAILED|CANCELLED") String status,
            @Size(max = 64) String stage,
            @Min(0) @Max(100) Integer progress,
            JsonNode payload,
            JsonNode output_payload,
            JsonNode citations,
            JsonNode metrics,
            @Size(max = 128) String error_code,
            @Size(max = 2000) String error_message
    ) {
    }

    public record LeaseClaimRequest(
            @NotBlank String tenant_id,
            @NotBlank @Size(max = 200) String worker_id,
            @NotBlank @Size(max = 64) String lease_token
    ) {
    }

    public record LeaseMutationRequest(
            @NotBlank String tenant_id,
            @NotBlank @Size(max = 64) String lease_token
    ) {
    }

    public record InternalApprovalSuspendRequest(
            @NotBlank String tenant_id,
            @NotBlank @Size(max = 64) String lease_token,
            @NotBlank @Size(max = 128) String node_id,
            @Pattern(regexp = "human\\.approval") String node_type,
            @NotBlank @Size(max = 160) String title,
            @Size(max = 2000) String instructions,
            @NotNull @Size(min = 1, max = 8) List<@Pattern(regexp = "[A-Za-z0-9_]{1,64}") String> assignee_roles,
            @Min(60) @Max(604800) int timeout_seconds,
            @Pattern(regexp = "approve|reject") String timeout_action,
            @NotNull JsonNode checkpoint,
            @Min(0) @Max(99) int progress
    ) {
    }

    public record ApprovalDecisionRequest(
            @Pattern(regexp = "approve|reject") String decision,
            @Size(max = 1000) String reason,
            @Min(0) long revision
    ) {
    }

    public record ConversationView(
            String conversation_id, String tenant_id, String title, String status,
            String created_by, Instant created_at, Instant updated_at
    ) {
    }

    public record MessageView(
            String message_id, String tenant_id, String conversation_id, String role,
            JsonNode content, String related_run_id, Instant created_at
    ) {
    }

    public record RunView(
            String run_id, String tenant_id, String conversation_id,
            String workflow_id, String workflow_version_id, int workflow_version_no, String workflow_checksum,
            String status, String stage, int progress, JsonNode input_payload, JsonNode output_payload,
            JsonNode citations, JsonNode metrics, String error_code, String error_message,
            String created_by, Instant started_at, Instant finished_at, Instant created_at, Instant updated_at,
            long revision
    ) {
    }

    public record EventView(
            String event_id, String external_event_id, String tenant_id, String run_id, long sequence_no,
            String event_type, String status, String stage, int progress, JsonNode payload,
            String source_service, Instant created_at
    ) {
    }

    public record NodeExecutionView(
            String node_execution_id, String tenant_id, String run_id, String node_id, String node_type,
            int ordinal, String status, Instant started_at, Instant completed_at, Long duration_ms,
            JsonNode details, String error_message, List<NodeAttemptView> attempts, Instant created_at, Instant updated_at
    ) {
    }

    public record NodeAttemptView(
            String node_attempt_id, int attempt_no, String status, Instant started_at, Instant completed_at,
            Long duration_ms, boolean retryable, long retry_delay_ms, String error_type, String error_message,
            JsonNode details
    ) {
    }

    public record ExecutionContextView(
            String run_id, String tenant_id, String actor_id, String conversation_id, String status,
            String workflow_id, String workflow_version_id, int workflow_version_no, String workflow_checksum,
            JsonNode workflow_dsl, JsonNode workflow_layout, JsonNode input_payload, JsonNode approval_resume
    ) {
    }

    public record ApprovalView(
            String approval_id, String tenant_id, String run_id, String node_id, String node_type,
            String title, String instructions, List<String> assignee_roles, String status,
            String decision, String decision_reason, String decided_by, String timeout_action,
            Instant expires_at, Instant decided_at, boolean timed_out,
            Instant created_at, Instant updated_at, long revision
    ) {
    }

    public record ApprovalStatsView(
            String health, long pending_count, long due_soon_count, long overdue_count,
            long oldest_pending_age_seconds, long decided_count_24h, long timed_out_count_24h,
            double timeout_rate_24h, long average_decision_seconds_24h,
            long due_within_seconds, Instant generated_at
    ) {
    }

    public record ApprovalTrendPointView(
            java.time.LocalDate date, long created_count, long decided_count,
            long approved_count, long rejected_count, long timed_out_count,
            long cancelled_count, long average_decision_seconds
    ) {
    }

    public record ApprovalTrendView(
            int days, java.time.LocalDate from_date, java.time.LocalDate to_date,
            String timezone, Instant generated_at, List<ApprovalTrendPointView> items
    ) {
    }

    public record LeaseView(
            String run_id, String tenant_id, boolean acquired, String reason,
            String worker_id, int attempt_no, Instant expires_at,
            long lease_duration_seconds, long heartbeat_interval_seconds
    ) {
    }

    public record DispatchOutboxView(
            String outbox_id, String tenant_id, String run_id, String event_type, String status,
            int attempt_count, int max_attempts, Instant available_at, String locked_by,
            Instant locked_until, String last_error, Instant published_at, Instant dead_lettered_at,
            Instant created_at, Instant updated_at, long revision
    ) {
    }

    public record DeadLetterIngestRequest(
            @NotBlank String tenant_id,
            @NotBlank String run_id,
            @NotBlank @Size(max = 128) String failed_task_id,
            @NotBlank @Size(max = 128) String source_queue,
            @Size(max = 2000) String error,
            JsonNode payload
    ) {
    }

    public record DeadLetterView(
            String dead_letter_id, String tenant_id, String run_id, String failed_task_id,
            String source_queue, String error, JsonNode payload, String status, int replay_count,
            String last_error, Instant replay_requested_at, Instant replayed_at, String replayed_by,
            Instant created_at, Instant updated_at, long revision
    ) {
    }

    public record DeadLetterAuditView(
            String audit_id, String dead_letter_id, String tenant_id, String run_id,
            String action, String actor_id, JsonNode detail, Instant created_at
    ) {
    }

    public record ConversationResponse(String status, ConversationView item) {
    }
    public record ConversationListResponse(String status, List<ConversationView> items) {
    }
    public record MessageListResponse(String status, List<MessageView> items) {
    }
    public record MessageRunResponse(String status, MessageView message, RunView run) {
    }
    public record RunResponse(String status, RunView item) {
    }
    public record RunListResponse(String status, List<RunView> items) {
    }
    public record EventResponse(String status, EventView item, boolean duplicate) {
    }
    public record EventListResponse(String status, List<EventView> items) {
    }
    public record NodeExecutionListResponse(String status, List<NodeExecutionView> items) {
    }
    public record ExecutionContextResponse(String status, ExecutionContextView item) {
    }
    public record LeaseResponse(String status, LeaseView item) {
    }
    public record ApprovalResponse(String status, ApprovalView item, boolean duplicate) {
    }
    public record ApprovalListResponse(String status, List<ApprovalView> items) {
    }
    public record ApprovalStatsResponse(String status, ApprovalStatsView item) {
    }
    public record ApprovalTrendResponse(String status, ApprovalTrendView item) {
    }
    public record DispatchOutboxResponse(String status, DispatchOutboxView item) {
    }
    public record DispatchOutboxListResponse(String status, List<DispatchOutboxView> items) {
    }
    public record DeadLetterResponse(String status, DeadLetterView item, boolean duplicate) {
    }
    public record DeadLetterListResponse(String status, List<DeadLetterView> items) {
    }
    public record DeadLetterAuditListResponse(String status, List<DeadLetterAuditView> items) {
    }
    public record DeleteResponse(String status, String deleted_conversation_id) {
    }
}
