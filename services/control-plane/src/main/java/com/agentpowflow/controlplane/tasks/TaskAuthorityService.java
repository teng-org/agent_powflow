package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.Locale;

@Service
public class TaskAuthorityService {
    private static final Set<String> ACTIVE = Set.of("PENDING", "RUNNING", "CANCELLING");
    public static final Set<String> TERMINAL_STATUSES = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private static final Map<String, Set<String>> TRANSITIONS = Map.of(
            "PENDING", Set.of("PENDING", "RUNNING", "CANCELLING", "FAILED", "CANCELLED"),
            "RUNNING", Set.of("RUNNING", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED"),
            "CANCELLING", Set.of("CANCELLING", "FAILED", "CANCELLED"),
            "SUCCEEDED", Set.of("SUCCEEDED"),
            "FAILED", Set.of("FAILED"),
            "CANCELLED", Set.of("CANCELLED")
    );

    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final KnowledgeExecutionClient executionClient;
    private final TaskBatchAggregateService batchAggregateService;
    private final TransactionTemplate transactionTemplate;

    public TaskAuthorityService(
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            KnowledgeExecutionClient executionClient,
            TaskBatchAggregateService batchAggregateService,
            PlatformTransactionManager transactionManager
    ) {
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.executionClient = executionClient;
        this.batchAggregateService = batchAggregateService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    @Transactional(readOnly = true)
    public List<TaskDto.TaskView> list(String tenantId, String kbId, int limit) {
        String tenant = requireText(tenantId, "tenant id missing");
        PageRequest page = PageRequest.of(0, bounded(limit, 300));
        List<TaskEntity> tasks = kbId == null || kbId.isBlank()
                ? taskRepository.findByTenantIdOrderByUpdatedAtDesc(tenant, page)
                : taskRepository.findByTenantIdAndKbIdOrderByUpdatedAtDesc(tenant, kbId.trim(), page);
        return tasks.stream().map(this::toView).toList();
    }

    @Transactional(readOnly = true)
    public TaskDto.StatusResponse status(String tenantId, String taskId) {
        return taskRepository.findByTaskIdAndTenantId(requireText(taskId, "task id missing"), requireText(tenantId, "tenant id missing"))
                .map(task -> new TaskDto.StatusResponse("ok", taskId, task.getStatus(), toView(task)))
                .orElseGet(() -> new TaskDto.StatusResponse("ok", taskId, "UNKNOWN", null));
    }

    @Transactional(readOnly = true)
    public List<TaskDto.EventView> events(String tenantId, String taskId, int limit) {
        TaskEntity task = requireTask(tenantId, taskId);
        return eventRepository.findByTaskIdAndTenantIdOrderByEventIdDesc(
                        task.getTaskId(), task.getTenantId(), PageRequest.of(0, bounded(limit, 500)))
                .stream().map(this::toEventView).toList();
    }

    public TaskDto.CancelResponse cancel(String tenantId, String taskId) {
        CancelState state = transactionTemplate.execute(status -> cancelInTransaction(tenantId, taskId));
        if (state == null) throw new IllegalStateException("task cancellation transaction returned no state");
        boolean dispatched = state.cancelledTaskIds().isEmpty() || executionClient.revokeTasks(state.cancelledTaskIds());
        return new TaskDto.CancelResponse(
                "ok", state.taskId(), state.state(), state.cancelledTaskIds(), dispatched
        );
    }

    @Transactional
    public InternalEventResult appendInternalEvent(String taskId, TaskDto.InternalTaskEventRequest request) {
        String tenant = requireText(request.tenant_id(), "tenant id missing");
        String eventKey = requireText(request.event_id(), "event id missing");
        taskRepository.lockCommandScope("task-event\u001f" + tenant + "\u001f" + eventKey);
        TaskEventEntity duplicate = eventRepository.findByTenantIdAndEventKey(tenant, eventKey).orElse(null);
        if (duplicate != null) {
            if (!taskId.equals(duplicate.getTaskId())) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "event id already belongs to another task");
            }
            TaskEntity task = requireTask(tenant, taskId);
            return new InternalEventResult(toView(task), toEventView(duplicate), true);
        }

        TaskEntity task = taskRepository.lockByTaskIdAndTenantId(taskId, tenant).orElse(null);
        String eventType = requireText(request.event_type(), "event type missing").toUpperCase(Locale.ROOT);
        if (task == null) {
            if (!"TASK_ENSURE".equals(eventType)) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found");
            }
            task = new TaskEntity();
            task.setTaskId(requireText(taskId, "task id missing"));
            task.setTenantId(tenant);
            task.setKbId(requireText(request.kb_id(), "knowledge base id missing"));
            task.setDocId(request.doc_id() == null ? "" : request.doc_id().trim());
            task.setTaskType(requireText(request.task_type(), "task type missing").toUpperCase(Locale.ROOT));
            task.setFilename(request.filename() == null ? "" : request.filename().trim());
            task.setStatus("PENDING");
            task.setStage(request.stage().trim().toUpperCase(Locale.ROOT));
        } else {
            verifyIdentity(task, request);
            if ((task.getFilename() == null || task.getFilename().isBlank())
                    && request.filename() != null && !request.filename().isBlank()) {
                task.setFilename(request.filename().trim());
            }
        }

        String nextStatus = request.status() == null || request.status().isBlank()
                ? task.getStatus() : request.status().trim().toUpperCase(Locale.ROOT);
        String currentStatus = task.getStatus().toUpperCase(Locale.ROOT);
        String nextStage = requireText(request.stage(), "task stage missing").toUpperCase(Locale.ROOT);
        if (!TRANSITIONS.getOrDefault(currentStatus, Set.of()).contains(nextStatus)) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "task status cannot transition from " + currentStatus + " to " + nextStatus
            );
        }
        if (TERMINAL_STATUSES.contains(currentStatus)) {
            boolean stateMutation = !task.getStage().equals(nextStage)
                    || request.progress_current() != null && request.progress_current() > task.getProgressCurrent()
                    || request.progress_total() != null && request.progress_total() > task.getProgressTotal()
                    || request.progress_pct() != null && request.progress_pct() > task.getProgressPct()
                    || request.error_message() != null && !java.util.Objects.equals(request.error_message(), task.getErrorMessage());
            if (stateMutation) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "terminal task state is immutable");
            }
        }
        task.setStatus(nextStatus);
        task.setStage(nextStage);
        if (request.progress_current() != null) {
            task.setProgressCurrent(Math.max(task.getProgressCurrent(), request.progress_current()));
        }
        if (request.progress_total() != null) {
            task.setProgressTotal(Math.max(task.getProgressTotal(), request.progress_total()));
        }
        if (request.progress_pct() != null) {
            task.setProgressPct(Math.max(task.getProgressPct(), Math.min(100, request.progress_pct())));
        }
        if (task.getProgressTotal() > 0 && task.getProgressCurrent() > task.getProgressTotal()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "task progress current cannot exceed total");
        }
        if (TERMINAL_STATUSES.contains(nextStatus) && task.getProgressTotal() > 0) {
            task.setProgressCurrent(task.getProgressTotal());
            task.setProgressPct(100);
        }
        if (request.error_message() != null) task.setErrorMessage(request.error_message());
        task.setStateOwner("CONTROL");
        TaskEntity savedTask = taskRepository.saveAndFlush(task);

        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(savedTask.getTaskId());
        event.setTenantId(savedTask.getTenantId());
        event.setKbId(savedTask.getKbId());
        event.setDocId(savedTask.getDocId());
        event.setEventKey(eventKey);
        event.setEventType(eventType);
        event.setStage(savedTask.getStage());
        Map<String, Object> payload = new LinkedHashMap<>(request.payload() == null ? Map.of() : request.payload());
        payload.putIfAbsent("source_service", request.source_service());
        event.setPayload(payload);
        TaskEventEntity savedEvent = eventRepository.saveAndFlush(event);
        batchAggregateService.refreshForTask(savedTask);
        return new InternalEventResult(toView(savedTask), toEventView(savedEvent), false);
    }

    @Transactional
    public TaskDto.ClearResponse clearFinished(String tenantId, String kbId) {
        String tenant = requireText(tenantId, "tenant id missing");
        List<TaskEntity> candidates = taskRepository.findByTenantIdAndStatusIn(tenant, TERMINAL_STATUSES);
        if (kbId != null && !kbId.isBlank()) {
            String targetKb = kbId.trim();
            candidates = candidates.stream().filter(task -> targetKb.equals(task.getKbId())).toList();
        }
        List<String> ids = candidates.stream().map(TaskEntity::getTaskId).toList();
        long deletedEvents = 0;
        if (!ids.isEmpty()) {
            deletedEvents = eventRepository.deleteByTenantIdAndTaskIdIn(tenant, ids);
            taskRepository.deleteDedupLocks(ids);
            taskRepository.deleteAllInBatch(candidates);
            taskRepository.deleteEmptyBatches(tenant);
        }
        Map<String, String> scope = new LinkedHashMap<>();
        scope.put("tenant_id", tenant);
        if (kbId != null && !kbId.isBlank()) scope.put("kb_id", kbId.trim());
        return new TaskDto.ClearResponse("ok", scope, new TaskDto.ClearResult(ids.size(), deletedEvents));
    }

    private CancelState cancelInTransaction(String tenantId, String taskId) {
        TaskEntity root = requireTask(tenantId, taskId);
        List<TaskEntity> targets = new ArrayList<>();
        if (ACTIVE.contains(root.getStatus())) targets.add(root);
        if (root.isBatchIsParent() && root.getBatchId() != null && !root.getBatchId().isBlank()) {
            targets.addAll(taskRepository.findByTenantIdAndBatchIdAndBatchIsParentFalseAndStatusIn(
                    root.getTenantId(), root.getBatchId(), ACTIVE
            ));
        }
        Instant now = Instant.now();
        for (TaskEntity task : targets) {
            task.setCancelRequested(true);
            task.setStatus("CANCELLED");
            task.setStage("CANCELLED");
            if (task.getProgressTotal() > 0) {
                task.setProgressCurrent(task.getProgressTotal());
                task.setProgressPct(100);
            }
            task.setStateOwner("CONTROL");
            task.setUpdatedAt(now);
            taskRepository.save(task);
            eventRepository.save(cancelEvent(task));
        }
        taskRepository.flush();
        eventRepository.flush();
        if (root.getBatchId() != null && !root.getBatchId().isBlank()) {
            batchAggregateService.refresh(root.getTenantId(), root.getBatchId());
        }
        List<String> ids = targets.stream().map(TaskEntity::getTaskId).distinct().toList();
        return new CancelState(root.getTaskId(), root.getStatus(), ids);
    }

    private TaskEventEntity cancelEvent(TaskEntity task) {
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(task.getTaskId());
        event.setTenantId(task.getTenantId());
        event.setKbId(task.getKbId());
        event.setDocId(task.getDocId());
        event.setEventType("TASK_CANCELLED");
        event.setEventKey("task:" + task.getTaskId() + ":cancelled");
        event.setStage("CANCELLED");
        event.setPayload(Map.of("status", "CANCELLED", "authority", "CONTROL"));
        return event;
    }

    private void verifyIdentity(TaskEntity task, TaskDto.InternalTaskEventRequest request) {
        String kbId = requireText(request.kb_id(), "knowledge base id missing");
        String docId = request.doc_id() == null ? "" : request.doc_id().trim();
        String taskType = request.task_type() == null ? task.getTaskType() : request.task_type().trim().toUpperCase(Locale.ROOT);
        if (!task.getKbId().equals(kbId) || !task.getDocId().equals(docId) || !task.getTaskType().equals(taskType)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task immutable identity mismatch");
        }
    }

    private TaskEntity requireTask(String tenantId, String taskId) {
        return taskRepository.findByTaskIdAndTenantId(
                        requireText(taskId, "task id missing"), requireText(tenantId, "tenant id missing"))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
    }

    private TaskDto.TaskView toView(TaskEntity task) {
        return new TaskDto.TaskView(
                task.getTaskId(), task.getTenantId(), task.getKbId(), task.getDocId(), task.getTaskType(),
                task.getStatus(), task.getStage(), task.getFilename(), task.getBatchId(), task.getBatchName(),
                task.isBatchIsParent(), task.getProgressCurrent(), task.getProgressTotal(), task.getProgressPct(),
                task.isCancelRequested(), task.getErrorMessage(), task.getStateOwner(), task.getCreatedAt(), task.getUpdatedAt()
        );
    }

    private TaskDto.EventView toEventView(TaskEventEntity event) {
        return new TaskDto.EventView(
                event.getEventId(), event.getTaskId(), event.getTenantId(), event.getKbId(), event.getDocId(),
                event.getEventType(), event.getStage(), event.getPayload(), event.getTimestamp()
        );
    }

    private int bounded(int value, int max) { return Math.max(1, Math.min(value, max)); }

    private String requireText(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }

    private record CancelState(String taskId, String state, List<String> cancelledTaskIds) {}
    public record InternalEventResult(TaskDto.TaskView task, TaskDto.EventView event, boolean duplicate) {}
}
