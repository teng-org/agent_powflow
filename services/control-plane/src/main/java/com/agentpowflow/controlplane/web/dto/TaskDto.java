package com.agentpowflow.controlplane.web.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class TaskDto {
    private TaskDto() {}

    public record IngestTextRequest(
            @Size(max = 128) String doc_id,
            @Size(max = 512) String filename,
            @NotBlank String content,
            @Min(100) @Max(2000) Integer chunk_size,
            @Min(0) @Max(500) Integer chunk_overlap,
            Boolean auto_summary
    ) {}

    public record IngestAcceptedResponse(
            String status, String task_id, String task_type, String kb_id, String doc_id,
            boolean deduplicated
    ) {}

    public record DocumentCommandRequest(
            @Size(max = 32) String chunk_method,
            @Size(max = 256) String chunk_delimiter,
            @Min(100) @Max(2000) Integer chunk_size,
            @Min(0) @Max(500) Integer chunk_overlap,
            @Size(max = 128) String target_kb_id,
            @Size(max = 50) List<@Size(max = 100) String> tags,
            @Size(max = 16) String mode
    ) {}

    public record DocumentCommandAcceptedResponse(
            String status, String task_id, String task_type, String kb_id, String doc_id,
            String command, boolean deduplicated, String trace_id
    ) {}

    public record BatchDocumentsRequest(
            @NotBlank @Size(max = 32) String operation,
            @Size(min = 1, max = 500) List<@NotBlank @Size(max = 128) String> doc_ids,
            @Size(max = 32) String chunk_method,
            @Size(max = 256) String chunk_delimiter,
            @Min(100) @Max(2000) Integer chunk_size,
            @Min(0) @Max(500) Integer chunk_overlap,
            @Size(max = 128) String target_kb_id,
            @Size(max = 50) List<@Size(max = 100) String> tags,
            @Size(max = 16) String mode,
            @Size(max = 256) String batch_name
    ) {}

    public record BatchAcceptedItem(
            String doc_id, String filename, String task_id, int index
    ) {}

    public record BatchAcceptedResponse(
            String status, String batch_id, String batch_name, String parent_task_id,
            String operation, int accepted_count, List<BatchAcceptedItem> accepted,
            String trace_id
    ) {}

    public record TaskBatchView(
            String batch_id, String tenant_id, String kb_id, String operation, String batch_name,
            Map<String, Object> params_json, String parent_task_id, int total_items,
            int succeeded_items, int failed_items, int cancelled_items, int running_items,
            String status, String created_by, Instant created_at, Instant updated_at
    ) {}

    public record TaskBatchDetailResponse(
            String status, TaskBatchView item, List<TaskView> items
    ) {}

    public record UploadAcceptedItem(
            String filename, String doc_id, String task_id, String process_mode
    ) {}

    public record UploadSkippedItem(
            String filename, String reason
    ) {}

    public record UploadResponse(
            String status, String kb_id, int accepted_count, int skipped_count,
            int max_files_per_request, long max_file_size_bytes, long max_batch_size_bytes,
            List<UploadAcceptedItem> accepted, List<UploadSkippedItem> skipped, String trace_id
    ) {}

    public record InternalTaskEventRequest(
            @NotBlank @Size(max = 160) String event_id,
            @NotBlank @Size(max = 64) String tenant_id,
            @NotBlank @Size(max = 128) String kb_id,
            @Size(max = 128) String doc_id,
            @Size(max = 64) String task_type,
            @Size(max = 512) String filename,
            @NotBlank @Size(max = 64) String event_type,
            @Size(max = 32) String status,
            @NotBlank @Size(max = 128) String stage,
            @Min(0) Integer progress_current,
            @Min(0) Integer progress_total,
            @Min(0) @Max(100) Integer progress_pct,
            @Size(max = 2000) String error_message,
            Map<String, Object> payload,
            @NotBlank @Size(max = 64) String source_service
    ) {}

    public record InternalEventResponse(String status, TaskView task, EventView event, boolean duplicate) {}

    public record TaskView(
            String task_id, String tenant_id, String kb_id, String doc_id, String task_type,
            String status, String stage, String filename, String batch_id, String batch_name,
            boolean batch_is_parent, int progress_current, int progress_total, int progress_pct,
            boolean cancel_requested, String error_message, String state_owner,
            Instant created_at, Instant updated_at
    ) {}

    public record EventView(
            long event_id, String task_id, String tenant_id, String kb_id, String doc_id,
            String event_type, String stage, Map<String, Object> payload, Instant ts
    ) {}

    public record DispatchOutboxView(
            String outbox_id, String tenant_id, String task_id, String command_type, String status,
            int attempt_count, int max_attempts, Instant available_at, String locked_by,
            Instant locked_until, String last_error, Instant published_at, Instant dead_lettered_at,
            Instant created_at, Instant updated_at, long revision
    ) {}

    public record ListResponse(String status, List<TaskView> items) {}
    public record EventListResponse(String status, String task_id, List<EventView> items) {}
    public record DispatchOutboxResponse(String status, DispatchOutboxView item) {}
    public record DispatchOutboxListResponse(String status, List<DispatchOutboxView> items) {}
    public record StatusResponse(String status, String task_id, String state, TaskView record) {}
    public record CancelResponse(
            String status, String task_id, String state, List<String> cancelled_task_ids,
            boolean revocation_dispatched
    ) {}
    public record ClearResult(long deleted_tasks, long deleted_events) {}
    public record ClearResponse(String status, Map<String, String> scope, ClearResult result) {}
}
