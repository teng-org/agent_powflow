package com.agentpowflow.controlplane.tasks;

import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import com.agentpowflow.controlplane.web.dto.TaskDto;

@Service
public class TaskDispatchOutboxService {
    private final TaskDispatchOutboxRepository repository;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final TaskDispatchProperties properties;
    private final TaskBatchAggregateService batchAggregateService;

    public TaskDispatchOutboxService(
            TaskDispatchOutboxRepository repository,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            TaskDispatchProperties properties,
            TaskBatchAggregateService batchAggregateService
    ) {
        this.repository = repository;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.properties = properties;
        this.batchAggregateService = batchAggregateService;
    }

    @Transactional
    public TaskDispatchOutboxEntity enqueue(String tenantId, String taskId, String commandType, String payloadJson) {
        return enqueueWithStatus(tenantId, taskId, commandType, payloadJson, "PENDING");
    }

    @Transactional
    public TaskDispatchOutboxEntity enqueueWithStatus(
            String tenantId, String taskId, String commandType, String payloadJson, String status
    ) {
        TaskDispatchOutboxEntity outbox = new TaskDispatchOutboxEntity();
        outbox.setTenantId(tenantId);
        outbox.setTaskId(taskId);
        outbox.setCommandType(commandType);
        outbox.setPayloadJson(payloadJson);
        outbox.setStatus(status);
        outbox.setMaxAttempts(Math.max(1, properties.getMaxAttempts()));
        return repository.save(outbox);
    }

    @Transactional
    public List<TaskDispatchOutboxEntity> claimBatch(String dispatcherId) {
        Instant now = Instant.now();
        List<TaskDispatchOutboxEntity> items = repository.lockDispatchable(
                now, Math.max(1, Math.min(properties.getBatchSize(), 100))
        );
        Instant lockedUntil = now.plus(Math.max(5, properties.getClaimSeconds()), ChronoUnit.SECONDS);
        for (TaskDispatchOutboxEntity item : items) {
            item.setStatus("IN_FLIGHT");
            item.setAttemptCount(item.getAttemptCount() + 1);
            item.setLockedBy(dispatcherId);
            item.setLockedUntil(lockedUntil);
        }
        return repository.saveAllAndFlush(items);
    }

    @Transactional
    public void markPublished(String outboxId, String dispatcherId) {
        TaskDispatchOutboxEntity item = owned(outboxId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setStatus("PUBLISHED");
        item.setPublishedAt(now);
        item.setAvailableAt(now);
        item.setLastError("");
        clearLock(item);
        repository.save(item);
    }

    @Transactional
    public void markFailure(String outboxId, String dispatcherId, Throwable failure) {
        TaskDispatchOutboxEntity item = owned(outboxId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setLastError(truncate(failure == null ? "dispatch failed" : failure.getMessage(), 2000));
        clearLock(item);
        if (item.getAttemptCount() >= item.getMaxAttempts()) {
            item.setStatus("DEAD_LETTER");
            item.setDeadLetteredAt(now);
            item.setAvailableAt(now);
            taskRepository.lockByTaskIdAndTenantId(item.getTaskId(), item.getTenantId()).ifPresent(task -> {
                if (!TaskAuthorityService.TERMINAL_STATUSES.contains(task.getStatus())) {
                    task.setStatus("FAILED");
                    task.setStage("DISPATCH_DEAD_LETTER");
                    task.setErrorMessage(item.getLastError());
                    task.setStateOwner("CONTROL");
                    taskRepository.save(task);
                    TaskEventEntity event = new TaskEventEntity();
                    event.setTaskId(task.getTaskId());
                    event.setTenantId(task.getTenantId());
                    event.setKbId(task.getKbId());
                    event.setDocId(task.getDocId());
                    event.setEventKey("task:" + task.getTaskId() + ":dispatch-dead-letter");
                    event.setEventType("TASK_DISPATCH_DEAD_LETTER");
                    event.setStage("DISPATCH_DEAD_LETTER");
                    event.setPayload(java.util.Map.of("error", item.getLastError()));
                    eventRepository.save(event);
                    batchAggregateService.refreshForTask(task);
                }
            });
        } else {
            item.setStatus("RETRY");
            item.setAvailableAt(now.plus(backoffSeconds(item.getAttemptCount()), ChronoUnit.SECONDS));
        }
        repository.save(item);
    }

    @Transactional(readOnly = true)
    public List<TaskDto.DispatchOutboxView> list(String tenantId, String status) {
        String tenant = required(tenantId, "tenant id missing");
        List<TaskDispatchOutboxEntity> items = status == null || status.isBlank()
                ? repository.findTop100ByTenantIdOrderByCreatedAtDesc(tenant)
                : repository.findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(tenant, normalizeStatus(status));
        return items.stream().map(this::view).toList();
    }

    @Transactional
    public TaskDto.DispatchOutboxView replay(String tenantId, String outboxId) {
        String tenant = required(tenantId, "tenant id missing");
        String id = required(outboxId, "outbox id missing");
        TaskDispatchOutboxEntity snapshot = repository.findByOutboxIdAndTenantId(id, tenant)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task dispatch outbox item not found"));
        if (!"DEAD_LETTER".equals(snapshot.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "only dead-letter task dispatches can be replayed");
        }
        TaskEntity task = taskRepository.lockByTaskIdAndTenantId(snapshot.getTaskId(), tenant)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
        TaskDispatchOutboxEntity item = repository.lockByOutboxId(id)
                .filter(candidate -> tenant.equals(candidate.getTenantId()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task dispatch outbox item not found"));
        if (!"DEAD_LETTER".equals(item.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task dispatch outbox item is no longer dead-lettered");
        }
        Instant now = Instant.now();
        item.setStatus("PENDING");
        item.setAttemptCount(0);
        item.setAvailableAt(now);
        item.setPublishedAt(null);
        item.setDeadLetteredAt(null);
        item.setLastError("");
        clearLock(item);
        task.setStatus("PENDING");
        task.setStage("QUEUED");
        task.setErrorMessage("");
        task.setCancelRequested(false);
        task.setStateOwner("CONTROL");
        taskRepository.save(task);
        batchAggregateService.refreshForTask(task);
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(task.getTaskId());
        event.setTenantId(task.getTenantId());
        event.setKbId(task.getKbId());
        event.setDocId(task.getDocId());
        event.setEventKey("task:" + task.getTaskId() + ":dispatch-replay:" + UUID.randomUUID());
        event.setEventType("TASK_DISPATCH_REPLAYED");
        event.setStage("QUEUED");
        event.setPayload(Map.of("authority", "CONTROL", "outbox_id", item.getOutboxId()));
        eventRepository.save(event);
        return view(repository.saveAndFlush(item));
    }

    @Transactional
    public TaskDto.DispatchOutboxView replayPlatform(String outboxId) {
        String id = required(outboxId, "outbox id missing");
        TaskDispatchOutboxEntity item = repository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task dispatch outbox item not found"));
        return replay(item.getTenantId(), id);
    }

    public TaskDto.DispatchOutboxView view(TaskDispatchOutboxEntity item) {
        return new TaskDto.DispatchOutboxView(
                item.getOutboxId(), item.getTenantId(), item.getTaskId(), item.getCommandType(), item.getStatus(),
                item.getAttemptCount(), item.getMaxAttempts(), item.getAvailableAt(), item.getLockedBy(),
                item.getLockedUntil(), item.getLastError(), item.getPublishedAt(), item.getDeadLetteredAt(),
                item.getCreatedAt(), item.getUpdatedAt(), item.getRowVersion()
        );
    }

    private String normalizeStatus(String status) {
        String value = status.trim().toUpperCase();
        if (!Set.of("WAITING_UPLOAD", "CLEANUP_PENDING", "COMPLETED", "PENDING", "IN_FLIGHT", "RETRY", "PUBLISHED", "DEAD_LETTER", "CANCELLED").contains(value)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid task dispatch outbox status");
        }
        return value;
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }

    private TaskDispatchOutboxEntity owned(String outboxId, String dispatcherId) {
        return repository.lockByOutboxId(outboxId)
                .filter(item -> "IN_FLIGHT".equals(item.getStatus()) && dispatcherId.equals(item.getLockedBy()))
                .orElse(null);
    }

    private long backoffSeconds(int attempt) {
        long base = Math.max(1, properties.getRetryBaseSeconds());
        int exponent = Math.max(0, Math.min(attempt - 1, 30));
        long value;
        try { value = Math.multiplyExact(base, 1L << exponent); }
        catch (ArithmeticException ignored) { value = Long.MAX_VALUE; }
        return Math.min(value, Math.max(base, properties.getRetryMaxSeconds()));
    }

    private void clearLock(TaskDispatchOutboxEntity item) {
        item.setLockedBy("");
        item.setLockedUntil(null);
    }

    private String truncate(String value, int max) {
        String safe = value == null ? "" : value;
        return safe.length() <= max ? safe : safe.substring(0, max);
    }
}
