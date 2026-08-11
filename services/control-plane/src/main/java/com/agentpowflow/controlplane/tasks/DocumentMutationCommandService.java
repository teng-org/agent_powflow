package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
public class DocumentMutationCommandService {
    private static final Set<String> ACTIVE = Set.of("PENDING", "RUNNING", "CANCELLING");
    private static final Set<String> CHUNK_METHODS = Set.of("fixed", "sentence", "paragraph", "markdown", "delimiter");
    private static final Map<String, String> TASK_TYPES = Map.of(
            "summarize", "DOC_SUMMARIZE",
            "chunk", "DOC_CHUNK",
            "reindex", "DOC_REINDEX",
            "commit", "CHUNK_COMMIT",
            "move", "DOC_MOVE",
            "tag", "DOC_TAG",
            "delete", "DOC_DELETE"
    );

    private final KnowledgeBaseRepository knowledgeBaseRepository;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final TaskDispatchOutboxService outboxService;
    private final ObjectMapper objectMapper;

    public DocumentMutationCommandService(
            KnowledgeBaseRepository knowledgeBaseRepository,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            TaskDispatchOutboxService outboxService,
            ObjectMapper objectMapper
    ) {
        this.knowledgeBaseRepository = knowledgeBaseRepository;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.outboxService = outboxService;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public TaskDto.DocumentCommandAcceptedResponse summarizeKnowledgeBase(
            String tenantId, String actorId, String traceId, String kbId
    ) {
        String tenant = required(tenantId, "tenant id missing");
        String targetKb = required(kbId, "knowledge base id missing");
        KnowledgeBaseEntity kb = requireWritableKb(tenant, targetKb);
        String taskType = "KB_SUMMARIZE";
        taskRepository.lockCommandScope(tenant + "\u001f" + targetKb + "\u001fKB_SUMMARIZE");
        Optional<TaskEntity> existing = taskRepository
                .findFirstByTenantIdAndKbIdAndDocIdAndTaskTypeAndStatusInOrderByCreatedAtAsc(
                        tenant, targetKb, "", taskType, ACTIVE
                );
        if (existing.isPresent()) return response(existing.get(), "summarize", true, traceId);

        Map<String, Object> payload = basePayload(tenant, actorId, traceId, targetKb, "", taskType);
        payload.put("kb_name", kb.getName());
        TaskEntity task = createTask(tenant, targetKb, "", taskType, kb.getName());
        persistCreated(task, payload, "KB_SUMMARIZE");
        return response(task, "summarize", false, traceId);
    }

    @Transactional
    public TaskDto.DocumentCommandAcceptedResponse submitDocument(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            String docId,
            String command,
            TaskDto.DocumentCommandRequest request
    ) {
        String tenant = required(tenantId, "tenant id missing");
        String targetKb = required(kbId, "knowledge base id missing");
        String targetDoc = required(docId, "document id missing");
        requireWritableKb(tenant, targetKb);
        String operation = required(command, "document command missing").toLowerCase(Locale.ROOT);
        String taskType = TASK_TYPES.get(operation);
        if (taskType == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported document command");
        TaskDto.DocumentCommandRequest safeRequest = request == null
                ? new TaskDto.DocumentCommandRequest(null, null, null, null, null, null, null)
                : request;

        Map<String, Object> payload = basePayload(tenant, actorId, traceId, targetKb, targetDoc, taskType);
        payload.put("command", operation);
        validateAndAddParameters(tenant, targetKb, operation, safeRequest, payload);

        taskRepository.lockCommandScope(tenant + "\u001f" + targetKb + "\u001f" + targetDoc + "\u001fDOCUMENT_MUTATION");
        Optional<TaskEntity> existing = taskRepository
                .findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                        tenant, targetKb, targetDoc, ACTIVE
                );
        if (existing.isPresent()) {
            TaskEntity active = existing.get();
            if (!taskType.equals(active.getTaskType())) {
                throw new ResponseStatusException(
                        HttpStatus.CONFLICT, "another active document command already owns this document"
                );
            }
            return response(active, operation, true, traceId);
        }

        TaskEntity task = createTask(tenant, targetKb, targetDoc, taskType, "");
        persistCreated(task, payload, "DOCUMENT_COMMAND");
        return response(task, operation, false, traceId);
    }

    private void validateAndAddParameters(
            String tenant,
            String kbId,
            String operation,
            TaskDto.DocumentCommandRequest request,
            Map<String, Object> payload
    ) {
        if (Set.of("chunk", "reindex").contains(operation)) {
            String method = request.chunk_method() == null || request.chunk_method().isBlank()
                    ? "sentence" : request.chunk_method().trim().toLowerCase(Locale.ROOT);
            if (!CHUNK_METHODS.contains(method)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported chunk method");
            }
            int size = request.chunk_size() == null ? 500 : request.chunk_size();
            int overlap = request.chunk_overlap() == null ? 50 : request.chunk_overlap();
            if (overlap >= size) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_overlap must be smaller than chunk_size");
            }
            String delimiter = request.chunk_delimiter() == null ? "" : request.chunk_delimiter();
            if ("delimiter".equals(method) && delimiter.isBlank()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_delimiter is required for delimiter method");
            }
            payload.put("chunk_method", method);
            payload.put("chunk_size", size);
            payload.put("chunk_overlap", overlap);
            payload.put("chunk_delimiter", delimiter);
            payload.put("auto_summary", false);
        }
        if ("move".equals(operation)) {
            String target = required(request.target_kb_id(), "target_kb_id is required");
            if (kbId.equals(target)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "target knowledge base must differ from source");
            }
            requireWritableKb(tenant, target);
            payload.put("target_kb_id", target);
        }
        if ("tag".equals(operation)) {
            LinkedHashSet<String> normalized = new LinkedHashSet<>();
            for (String value : request.tags() == null ? List.<String>of() : request.tags()) {
                String tag = value == null ? "" : value.trim();
                if (!tag.isBlank()) normalized.add(tag);
            }
            if (normalized.isEmpty()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "tags is required");
            }
            String mode = request.mode() == null || request.mode().isBlank()
                    ? "add" : request.mode().trim().toLowerCase(Locale.ROOT);
            if (!Set.of("add", "replace").contains(mode)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "tag mode must be add or replace");
            }
            payload.put("tags", List.copyOf(normalized));
            payload.put("tag_mode", mode);
        }
    }

    private KnowledgeBaseEntity requireWritableKb(String tenant, String kbId) {
        KnowledgeBaseEntity kb = knowledgeBaseRepository.findByTenantIdAndKbId(tenant, kbId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found"));
        if (kb.isFrozen() || !"ACTIVE".equalsIgnoreCase(kb.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "knowledge base is not writable");
        }
        return kb;
    }

    private TaskEntity createTask(String tenant, String kbId, String docId, String taskType, String filename) {
        TaskEntity task = new TaskEntity();
        task.setTaskId(UUID.randomUUID().toString());
        task.setTenantId(tenant);
        task.setKbId(kbId);
        task.setDocId(docId);
        task.setTaskType(taskType);
        task.setStatus("PENDING");
        task.setStage("QUEUED");
        task.setFilename(filename == null ? "" : filename);
        task.setStateOwner("CONTROL");
        return taskRepository.save(task);
    }

    private void persistCreated(TaskEntity task, Map<String, Object> payload, String commandType) {
        payload.put("task_id", task.getTaskId());
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(task.getTaskId());
        event.setTenantId(task.getTenantId());
        event.setKbId(task.getKbId());
        event.setDocId(task.getDocId());
        event.setEventKey("task:" + task.getTaskId() + ":created");
        event.setEventType("TASK_CREATED");
        event.setStage("QUEUED");
        event.setPayload(Map.of("task_type", task.getTaskType(), "authority", "CONTROL"));
        eventRepository.save(event);
        outboxService.enqueue(task.getTenantId(), task.getTaskId(), commandType, writeJson(payload));
    }

    private Map<String, Object> basePayload(
            String tenant, String actorId, String traceId, String kbId, String docId, String taskType
    ) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("task_type", taskType);
        payload.put("tenant_id", tenant);
        payload.put("actor_id", required(actorId, "actor id missing"));
        payload.put("trace_id", required(traceId, "trace id missing"));
        payload.put("source_service", "control-plane");
        payload.put("kb_id", kbId);
        payload.put("doc_id", docId);
        return payload;
    }

    private TaskDto.DocumentCommandAcceptedResponse response(
            TaskEntity task, String command, boolean deduplicated, String traceId
    ) {
        return new TaskDto.DocumentCommandAcceptedResponse(
                "accepted", task.getTaskId(), task.getTaskType(), task.getKbId(), task.getDocId(),
                command, deduplicated, traceId
        );
    }

    private String writeJson(Map<String, Object> payload) {
        try { return objectMapper.writeValueAsString(payload); }
        catch (JsonProcessingException exception) {
            throw new IllegalStateException("document command serialization failed", exception);
        }
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }
}
