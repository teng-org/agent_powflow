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

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
public class DocumentIngestCommandService {
    private static final Set<String> ACTIVE = Set.of("PENDING", "RUNNING", "CANCELLING");
    private final KnowledgeBaseRepository knowledgeBaseRepository;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final TaskDispatchOutboxService outboxService;
    private final TaskDispatchProperties properties;
    private final ObjectMapper objectMapper;

    public DocumentIngestCommandService(
            KnowledgeBaseRepository knowledgeBaseRepository,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            TaskDispatchOutboxService outboxService,
            TaskDispatchProperties properties,
            ObjectMapper objectMapper
    ) {
        this.knowledgeBaseRepository = knowledgeBaseRepository;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.outboxService = outboxService;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public TaskDto.IngestAcceptedResponse ingestText(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            TaskDto.IngestTextRequest request
    ) {
        String tenant = required(tenantId, "tenant id missing");
        String targetKb = required(kbId, "knowledge base id missing");
        KnowledgeBaseEntity kb = knowledgeBaseRepository.findByTenantIdAndKbId(tenant, targetKb)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found"));
        if (kb.isFrozen() || !"ACTIVE".equalsIgnoreCase(kb.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "knowledge base is not writable");
        }
        String content = request.content();
        int contentBytes = content.getBytes(StandardCharsets.UTF_8).length;
        int maxBytes = Math.max(1024, properties.getMaxTextBytes());
        if (contentBytes > maxBytes) {
            throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE, "ingest text exceeds configured byte limit");
        }
        int chunkSize = request.chunk_size() == null ? 500 : request.chunk_size();
        int chunkOverlap = request.chunk_overlap() == null ? 50 : request.chunk_overlap();
        if (chunkOverlap >= chunkSize) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_overlap must be smaller than chunk_size");
        }
        String docId = request.doc_id() == null || request.doc_id().isBlank()
                ? UUID.randomUUID().toString() : request.doc_id().trim();
        String scope = tenant + "\u001f" + targetKb + "\u001f" + docId + "\u001fDOC_INGEST";
        taskRepository.lockCommandScope(scope);
        Optional<TaskEntity> existing = taskRepository
                .findFirstByTenantIdAndKbIdAndDocIdAndTaskTypeAndStatusInOrderByCreatedAtAsc(
                        tenant, targetKb, docId, "DOC_INGEST", ACTIVE
                );
        if (existing.isPresent()) {
            TaskEntity task = existing.get();
            return new TaskDto.IngestAcceptedResponse(
                    "accepted", task.getTaskId(), task.getTaskType(), targetKb, docId, true
            );
        }

        String filename = request.filename() == null || request.filename().isBlank()
                ? docId + ".txt" : request.filename().trim();
        String taskId = UUID.randomUUID().toString();
        TaskEntity task = new TaskEntity();
        task.setTaskId(taskId);
        task.setTenantId(tenant);
        task.setKbId(targetKb);
        task.setDocId(docId);
        task.setTaskType("DOC_INGEST");
        task.setStatus("PENDING");
        task.setStage("QUEUED");
        task.setFilename(filename);
        task.setStateOwner("CONTROL");
        taskRepository.save(task);

        TaskEventEntity created = new TaskEventEntity();
        created.setTaskId(taskId);
        created.setTenantId(tenant);
        created.setKbId(targetKb);
        created.setDocId(docId);
        created.setEventKey("task:" + taskId + ":created");
        created.setEventType("TASK_CREATED");
        created.setStage("QUEUED");
        created.setPayload(Map.of("task_type", "DOC_INGEST", "authority", "CONTROL"));
        eventRepository.save(created);

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("task_id", taskId);
        payload.put("task_type", "DOC_INGEST");
        payload.put("tenant_id", tenant);
        payload.put("actor_id", required(actorId, "actor id missing"));
        payload.put("trace_id", required(traceId, "trace id missing"));
        payload.put("source_service", "control-plane");
        payload.put("kb_id", targetKb);
        payload.put("doc_id", docId);
        payload.put("filename", filename);
        payload.put("content", content);
        payload.put("chunk_method", "fixed");
        payload.put("chunk_size", chunkSize);
        payload.put("chunk_overlap", chunkOverlap);
        payload.put("auto_summary", Boolean.TRUE.equals(request.auto_summary()));
        outboxService.enqueue(tenant, taskId, "DOC_INGEST_TEXT", writeJson(payload));

        return new TaskDto.IngestAcceptedResponse("accepted", taskId, "DOC_INGEST", targetKb, docId, false);
    }

    private String writeJson(Map<String, Object> payload) {
        try { return objectMapper.writeValueAsString(payload); }
        catch (JsonProcessingException exception) { throw new IllegalStateException("task command serialization failed", exception); }
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }
}
