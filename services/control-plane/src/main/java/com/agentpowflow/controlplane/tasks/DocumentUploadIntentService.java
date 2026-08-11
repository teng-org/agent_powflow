package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class DocumentUploadIntentService {
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {};
    private final KnowledgeBaseRepository knowledgeBaseRepository;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final TaskDispatchOutboxRepository outboxRepository;
    private final TaskDispatchOutboxService outboxService;
    private final TaskDispatchProperties properties;
    private final ObjectMapper objectMapper;

    public DocumentUploadIntentService(
            KnowledgeBaseRepository knowledgeBaseRepository,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            TaskDispatchOutboxRepository outboxRepository,
            TaskDispatchOutboxService outboxService,
            TaskDispatchProperties properties,
            ObjectMapper objectMapper
    ) {
        this.knowledgeBaseRepository = knowledgeBaseRepository;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.outboxRepository = outboxRepository;
        this.outboxService = outboxService;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public UploadIntent create(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            String filename,
            String processMode,
            String chunkMethod,
            String chunkDelimiter,
            int chunkSize,
            int chunkOverlap,
            boolean autoSummary
    ) {
        String tenant = required(tenantId, "tenant id missing");
        String targetKb = required(kbId, "knowledge base id missing");
        writableKb(tenant, targetKb);
        String taskId = UUID.randomUUID().toString();
        String docId = UUID.randomUUID().toString();
        String mode = required(processMode, "process mode missing");
        boolean indexed = "UPLOAD_CLEAN_CHUNK_EMBED".equals(mode);
        TaskEntity task = new TaskEntity();
        task.setTaskId(taskId);
        task.setTenantId(tenant);
        task.setKbId(targetKb);
        task.setDocId(docId);
        task.setTaskType(indexed ? "DOC_INGEST" : "DOC_UPLOAD");
        task.setStatus("PENDING");
        task.setStage("UPLOAD_PENDING");
        task.setFilename(required(filename, "filename missing"));
        task.setStateOwner("CONTROL");
        taskRepository.save(task);

        TaskEventEntity event = event(task, "task:" + taskId + ":created", "TASK_CREATED", "UPLOAD_PENDING");
        event.setPayload(Map.of(
                "task_type", task.getTaskType(), "authority", "CONTROL",
                "source", "MULTIPART", "process_mode", mode
        ));
        eventRepository.save(event);

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("task_id", taskId);
        payload.put("task_type", task.getTaskType());
        payload.put("tenant_id", tenant);
        payload.put("actor_id", required(actorId, "actor id missing"));
        payload.put("trace_id", required(traceId, "trace id missing"));
        payload.put("source_service", "control-plane");
        payload.put("kb_id", targetKb);
        payload.put("doc_id", docId);
        payload.put("filename", task.getFilename());
        payload.put("process_mode", mode);
        payload.put("content", "");
        payload.put("chunk_method", chunkMethod);
        payload.put("chunk_delimiter", chunkDelimiter);
        payload.put("chunk_size", chunkSize);
        payload.put("chunk_overlap", chunkOverlap);
        payload.put("auto_summary", autoSummary);
        TaskDispatchOutboxEntity outbox = outboxService.enqueueWithStatus(
                tenant, taskId, indexed ? "DOC_INGEST_OBJECT" : "DOC_UPLOAD_ARTIFACT",
                writeJson(payload), "WAITING_UPLOAD"
        );
        return new UploadIntent(taskId, docId, outbox.getOutboxId(), mode);
    }

    @Transactional
    public void activate(String tenantId, UploadIntent intent, Map<String, Object> staged) {
        if (!"UPLOAD_CLEAN_CHUNK_EMBED".equals(intent.processMode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "non-index upload must not be dispatched");
        }
        TaskDispatchOutboxEntity outbox = lockWaiting(tenantId, intent);
        TaskEntity task = taskRepository.lockByTaskIdAndTenantId(intent.taskId(), tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "upload task not found"));
        if (!"PENDING".equals(task.getStatus()) || !"UPLOAD_PENDING".equals(task.getStage())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "upload task is no longer waiting for staging");
        }
        String stagedMode = staged.get("process_mode") == null ? "" : String.valueOf(staged.get("process_mode")).trim();
        if (!intent.processMode().equals(stagedMode)) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "staging response process mode mismatch");
        }
        Map<String, Object> payload = readJson(outbox.getPayloadJson());
        copyString(staged, payload, "object_bucket", true);
        copyString(staged, payload, "content_object_key", true);
        copyString(staged, payload, "content_object_sha256", true);
        copyString(staged, payload, "source_object_key", true);
        copyString(staged, payload, "source_object_sha256", true);
        copyNumber(staged, payload, "source_object_size_bytes");
        copyNumber(staged, payload, "extracted_object_size_bytes");
        outbox.setPayloadJson(writeJson(payload));
        outbox.setStatus("PENDING");
        outbox.setAvailableAt(Instant.now());
        outbox.setLastError("");
        task.setStage("QUEUED");
        task.setErrorMessage("");
        taskRepository.save(task);
        TaskEventEntity event = event(task, "task:" + task.getTaskId() + ":upload-staged", "TASK_UPLOAD_STAGED", "QUEUED");
        event.setPayload(Map.of("authority", "CONTROL", "outbox_id", outbox.getOutboxId()));
        eventRepository.save(event);
        outboxRepository.save(outbox);
    }

    @Transactional
    public void completeWithoutDispatch(String tenantId, UploadIntent intent, Map<String, Object> staged) {
        if ("UPLOAD_CLEAN_CHUNK_EMBED".equals(intent.processMode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "indexed upload must be dispatched");
        }
        TaskDispatchOutboxEntity outbox = lockWaiting(tenantId, intent);
        TaskEntity task = taskRepository.lockByTaskIdAndTenantId(intent.taskId(), tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "upload task not found"));
        if (!"DOC_UPLOAD".equals(task.getTaskType())
                || !"PENDING".equals(task.getStatus())
                || !"UPLOAD_PENDING".equals(task.getStage())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "upload task is no longer waiting for staging");
        }
        String stagedMode = staged.get("process_mode") == null ? "" : String.valueOf(staged.get("process_mode")).trim();
        if (!intent.processMode().equals(stagedMode)) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "staging response process mode mismatch");
        }
        Map<String, Object> payload = readJson(outbox.getPayloadJson());
        copyString(staged, payload, "object_bucket", true);
        copyString(staged, payload, "content_object_key", true);
        copyString(staged, payload, "content_object_sha256", true);
        copyString(staged, payload, "source_object_key", true);
        copyString(staged, payload, "source_object_sha256", true);
        copyNumber(staged, payload, "source_object_size_bytes");
        copyNumber(staged, payload, "extracted_object_size_bytes");
        outbox.setPayloadJson(writeJson(payload));
        outbox.setStatus("COMPLETED");
        outbox.setAvailableAt(Instant.now());
        outbox.setLastError("");
        task.setStatus("SUCCEEDED");
        task.setStage("UPLOAD_AND_CLEAN".equals(intent.processMode()) ? "CLEANED" : "UPLOADED_RAW");
        task.setProgressCurrent(1);
        task.setProgressTotal(1);
        task.setProgressPct(100);
        task.setErrorMessage("");
        taskRepository.save(task);
        TaskEventEntity event = event(
                task, "task:" + task.getTaskId() + ":upload-completed", "TASK_UPLOAD_COMPLETED", task.getStage()
        );
        event.setPayload(Map.of(
                "authority", "CONTROL", "outbox_id", outbox.getOutboxId(),
                "process_mode", intent.processMode()
        ));
        eventRepository.save(event);
        outboxRepository.save(outbox);
    }

    @Transactional
    public boolean fail(String tenantId, UploadIntent intent, String stage, String reason) {
        TaskDispatchOutboxEntity outbox = outboxRepository.lockByOutboxId(intent.outboxId())
                .filter(item -> tenantId.equals(item.getTenantId()) && "WAITING_UPLOAD".equals(item.getStatus()))
                .orElse(null);
        if (outbox == null) return false;
        TaskEntity task = taskRepository.lockByTaskIdAndTenantId(intent.taskId(), tenantId).orElse(null);
        String error = truncate(reason, 2000);
        outbox.setStatus("CLEANUP_PENDING");
        outbox.setLastError(error);
        outbox.setAvailableAt(Instant.now());
        if (task != null && !TaskAuthorityService.TERMINAL_STATUSES.contains(task.getStatus())) {
            task.setStatus("FAILED");
            task.setStage(stage);
            task.setErrorMessage(error);
            taskRepository.save(task);
            TaskEventEntity event = event(task, "task:" + task.getTaskId() + ":" + stage.toLowerCase(), "TASK_UPLOAD_FAILED", stage);
            event.setPayload(Map.of("error", error));
            eventRepository.save(event);
        }
        outboxRepository.save(outbox);
        return true;
    }

    @Transactional
    public List<CleanupTarget> expireStale() {
        long timeout = Math.max(60, properties.getUploadIntentTimeoutSeconds());
        Instant cutoff = Instant.now().minus(timeout, ChronoUnit.SECONDS);
        List<TaskDispatchOutboxEntity> expired = outboxRepository.lockExpiredUploadIntents(cutoff, 50);
        return expired.stream().map(outbox -> {
            TaskEntity task = taskRepository.lockByTaskIdAndTenantId(outbox.getTaskId(), outbox.getTenantId()).orElse(null);
            outbox.setStatus("CLEANUP_PENDING");
            outbox.setLastError("upload staging timed out");
            outbox.setAvailableAt(Instant.now());
            if (task != null && !TaskAuthorityService.TERMINAL_STATUSES.contains(task.getStatus())) {
                task.setStatus("FAILED");
                task.setStage("UPLOAD_TIMEOUT");
                task.setErrorMessage("upload staging timed out");
                taskRepository.save(task);
                TaskEventEntity event = event(task, "task:" + task.getTaskId() + ":upload-timeout", "TASK_UPLOAD_FAILED", "UPLOAD_TIMEOUT");
                event.setPayload(Map.of("error", "upload staging timed out"));
                eventRepository.save(event);
            }
            outboxRepository.save(outbox);
            return task == null ? null : new CleanupTarget(task.getTenantId(), task.getKbId(), task.getDocId(), task.getTaskId());
        }).filter(java.util.Objects::nonNull).toList();
    }

    @Transactional
    public List<CleanupTarget> pendingCleanup() {
        return outboxRepository.lockPendingUploadCleanup(50).stream().map(outbox ->
                taskRepository.findByTaskIdAndTenantId(outbox.getTaskId(), outbox.getTenantId())
                        .map(task -> new CleanupTarget(task.getTenantId(), task.getKbId(), task.getDocId(), task.getTaskId()))
                        .orElse(null)
        ).filter(java.util.Objects::nonNull).toList();
    }

    @Transactional
    public void markCleanupComplete(String tenantId, String taskId) {
        TaskDispatchOutboxEntity outbox = outboxRepository.findByTaskId(taskId)
                .flatMap(item -> outboxRepository.lockByOutboxId(item.getOutboxId()))
                .filter(item -> tenantId.equals(item.getTenantId()) && "CLEANUP_PENDING".equals(item.getStatus()))
                .orElse(null);
        if (outbox == null) return;
        outbox.setStatus("CANCELLED");
        outbox.setLastError("");
        outbox.setAvailableAt(Instant.now());
        outboxRepository.save(outbox);
    }

    @Transactional(readOnly = true)
    public void validateWritable(String tenantId, String kbId) {
        writableKb(required(tenantId, "tenant id missing"), required(kbId, "knowledge base id missing"));
    }

    private KnowledgeBaseEntity writableKb(String tenantId, String kbId) {
        KnowledgeBaseEntity kb = knowledgeBaseRepository.findByTenantIdAndKbId(tenantId, kbId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found"));
        if (kb.isFrozen() || !"ACTIVE".equalsIgnoreCase(kb.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "knowledge base is not writable");
        }
        return kb;
    }

    private TaskDispatchOutboxEntity lockWaiting(String tenantId, UploadIntent intent) {
        return outboxRepository.lockByOutboxId(intent.outboxId())
                .filter(item -> tenantId.equals(item.getTenantId())
                        && intent.taskId().equals(item.getTaskId())
                        && "WAITING_UPLOAD".equals(item.getStatus()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT, "upload intent is no longer waiting"));
    }

    private TaskEventEntity event(TaskEntity task, String key, String type, String stage) {
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(task.getTaskId());
        event.setTenantId(task.getTenantId());
        event.setKbId(task.getKbId());
        event.setDocId(task.getDocId());
        event.setEventKey(key);
        event.setEventType(type);
        event.setStage(stage);
        return event;
    }

    private void copyString(Map<String, Object> source, Map<String, Object> target, String key, boolean required) {
        String value = source.get(key) == null ? "" : String.valueOf(source.get(key)).trim();
        if (required && value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "staging response missing " + key);
        target.put(key, value);
    }

    private void copyNumber(Map<String, Object> source, Map<String, Object> target, String key) {
        Object value = source.get(key);
        if (!(value instanceof Number number) || number.longValue() < 0) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "staging response has invalid " + key);
        }
        target.put(key, number.longValue());
    }

    private Map<String, Object> readJson(String value) {
        try { return new LinkedHashMap<>(objectMapper.readValue(value, MAP_TYPE)); }
        catch (JsonProcessingException exception) { throw new IllegalStateException("task command payload is invalid", exception); }
    }

    private String writeJson(Map<String, Object> value) {
        try { return objectMapper.writeValueAsString(value); }
        catch (JsonProcessingException exception) { throw new IllegalStateException("task command serialization failed", exception); }
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }

    private String truncate(String value, int max) {
        String safe = value == null || value.isBlank() ? "upload staging failed" : value;
        return safe.length() <= max ? safe : safe.substring(0, max);
    }

    public record UploadIntent(String taskId, String docId, String outboxId, String processMode) {}
    public record CleanupTarget(String tenantId, String kbId, String docId, String taskId) {}
}
