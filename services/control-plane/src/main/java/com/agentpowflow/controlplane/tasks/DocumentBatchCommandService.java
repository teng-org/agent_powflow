package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class DocumentBatchCommandService {
    private static final Set<String> ACTIVE = Set.of("PENDING", "RUNNING", "CANCELLING");
    private static final Set<String> OPERATIONS = Set.of("chunk", "embed", "summarize", "move", "tag", "delete");
    private static final Set<String> CHUNK_METHODS = Set.of("fixed", "sentence", "paragraph", "markdown", "delimiter");
    private static final DateTimeFormatter BATCH_TIME = DateTimeFormatter.ofPattern("MMdd-HHmmss");

    private final KnowledgeBaseRepository knowledgeBaseRepository;
    private final KnowledgeExecutionClient executionClient;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;
    private final TaskBatchRepository batchRepository;
    private final TaskDispatchOutboxService outboxService;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactionTemplate;

    public DocumentBatchCommandService(
            KnowledgeBaseRepository knowledgeBaseRepository,
            KnowledgeExecutionClient executionClient,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository,
            TaskBatchRepository batchRepository,
            TaskDispatchOutboxService outboxService,
            ObjectMapper objectMapper,
            PlatformTransactionManager transactionManager
    ) {
        this.knowledgeBaseRepository = knowledgeBaseRepository;
        this.executionClient = executionClient;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
        this.batchRepository = batchRepository;
        this.outboxService = outboxService;
        this.objectMapper = objectMapper;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public TaskDto.BatchAcceptedResponse submit(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            TaskDto.BatchDocumentsRequest request
    ) {
        String tenant = required(tenantId, "tenant id missing");
        String actor = required(actorId, "actor id missing");
        String trace = required(traceId, "trace id missing");
        String sourceKb = required(kbId, "knowledge base id missing");
        requireWritableKb(tenant, sourceKb);
        NormalizedBatch normalized = normalize(tenant, sourceKb, request);
        Map<String, DocumentDescriptor> descriptors = preflight(tenant, sourceKb, normalized.docIds());
        TaskDto.BatchAcceptedResponse result = transactionTemplate.execute(status ->
                createBatch(tenant, actor, trace, sourceKb, normalized, descriptors)
        );
        if (result == null) throw new IllegalStateException("batch command transaction returned no result");
        return result;
    }

    @Transactional(readOnly = true)
    public TaskDto.TaskBatchDetailResponse detail(String tenantId, String batchId) {
        String tenant = required(tenantId, "tenant id missing");
        TaskBatchEntity batch = batchRepository.findByBatchIdAndTenantId(required(batchId, "batch id missing"), tenant)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task batch not found"));
        List<TaskDto.TaskView> tasks = taskRepository
                .findByTenantIdAndBatchIdOrderByBatchIsParentDescUpdatedAtDesc(tenant, batch.getBatchId())
                .stream().map(this::taskView).toList();
        return new TaskDto.TaskBatchDetailResponse("ok", batchView(batch), tasks);
    }

    public TaskDto.BatchAcceptedResponse retryFailed(
            String tenantId, String actorId, String traceId, String batchId
    ) {
        TaskDto.TaskBatchDetailResponse detail = detail(tenantId, batchId);
        List<String> failedDocIds = detail.items().stream()
                .filter(task -> !task.batch_is_parent() && "FAILED".equals(task.status()))
                .map(TaskDto.TaskView::doc_id).filter(value -> value != null && !value.isBlank()).toList();
        if (failedDocIds.isEmpty()) {
            TaskDto.TaskBatchView batch = detail.item();
            return new TaskDto.BatchAcceptedResponse(
                    "ok", batch.batch_id(), batch.batch_name(), batch.parent_task_id(), batch.operation(),
                    0, List.of(), traceId
            );
        }
        Map<String, Object> params = detail.item().params_json();
        TaskDto.BatchDocumentsRequest retry = new TaskDto.BatchDocumentsRequest(
                detail.item().operation(), failedDocIds,
                stringValue(params.get("chunk_method")), stringValue(params.get("chunk_delimiter")),
                intValue(params.get("chunk_size")), intValue(params.get("chunk_overlap")),
                stringValue(params.get("target_kb_id")), stringList(params.get("tags")),
                stringValue(params.get("mode")), detail.item().batch_name() + "-重试"
        );
        return submit(tenantId, actorId, traceId, detail.item().kb_id(), retry);
    }

    private TaskDto.BatchAcceptedResponse createBatch(
            String tenant,
            String actor,
            String trace,
            String kbId,
            NormalizedBatch normalized,
            Map<String, DocumentDescriptor> descriptors
    ) {
        List<String> lockOrder = normalized.docIds().stream().sorted().toList();
        for (String docId : lockOrder) {
            taskRepository.lockCommandScope(tenant + "\u001f" + kbId + "\u001f" + docId + "\u001fDOCUMENT_MUTATION");
        }
        List<String> conflicts = new ArrayList<>();
        for (String docId : normalized.docIds()) {
            if (taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                    tenant, kbId, docId, ACTIVE).isPresent()) {
                conflicts.add(docId);
            }
        }
        if (!conflicts.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "documents already have active commands: " + String.join(", ", conflicts.subList(0, Math.min(10, conflicts.size())))
            );
        }

        String batchId = UUID.randomUUID().toString();
        String parentTaskId = UUID.randomUUID().toString();
        TaskEntity parent = new TaskEntity();
        parent.setTaskId(parentTaskId);
        parent.setTenantId(tenant);
        parent.setKbId(kbId);
        parent.setDocId("");
        parent.setTaskType("BATCH_DOC_" + normalized.operation().toUpperCase(Locale.ROOT));
        parent.setStatus("RUNNING");
        parent.setStage("BATCH_RUNNING");
        parent.setFilename(normalized.batchName());
        parent.setBatchId(batchId);
        parent.setBatchName(normalized.batchName());
        parent.setBatchIsParent(true);
        parent.setProgressTotal(normalized.docIds().size());
        parent.setStateOwner("CONTROL");
        taskRepository.save(parent);
        appendCreated(parent);

        TaskBatchEntity batch = new TaskBatchEntity();
        batch.setBatchId(batchId);
        batch.setTenantId(tenant);
        batch.setKbId(kbId);
        batch.setOperation(normalized.operation());
        batch.setBatchName(normalized.batchName());
        batch.setParams(normalized.params());
        batch.setParentTaskId(parentTaskId);
        batch.setTotalItems(normalized.docIds().size());
        batch.setRunningItems(normalized.docIds().size());
        batch.setStatus("RUNNING");
        batch.setCreatedBy(actor);
        batchRepository.save(batch);

        List<TaskDto.BatchAcceptedItem> accepted = new ArrayList<>();
        int index = 0;
        for (String docId : normalized.docIds()) {
            index++;
            DocumentDescriptor descriptor = descriptors.get(docId);
            String taskType = taskType(normalized.operation(), descriptor.chunkCount());
            TaskEntity child = new TaskEntity();
            child.setTaskId(UUID.randomUUID().toString());
            child.setTenantId(tenant);
            child.setKbId(kbId);
            child.setDocId(docId);
            child.setTaskType(taskType);
            child.setStatus("PENDING");
            child.setStage("QUEUED");
            child.setFilename(descriptor.filename());
            child.setBatchId(batchId);
            child.setBatchName(normalized.batchName());
            child.setStateOwner("CONTROL");
            taskRepository.save(child);
            appendCreated(child);
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("task_id", child.getTaskId());
            payload.put("task_type", taskType);
            payload.put("tenant_id", tenant);
            payload.put("actor_id", actor);
            payload.put("trace_id", trace);
            payload.put("source_service", "control-plane");
            payload.put("kb_id", kbId);
            payload.put("doc_id", docId);
            payload.put("filename", descriptor.filename());
            payload.put("command", normalized.operation());
            payload.put("batch_id", batchId);
            payload.put("batch_name", normalized.batchName());
            payload.putAll(normalized.commandParameters());
            outboxService.enqueue(tenant, child.getTaskId(), "DOCUMENT_BATCH_ITEM", writeJson(payload));
            accepted.add(new TaskDto.BatchAcceptedItem(docId, descriptor.filename(), child.getTaskId(), index));
        }
        taskRepository.flush();
        batchRepository.flush();
        return new TaskDto.BatchAcceptedResponse(
                "accepted", batchId, normalized.batchName(), parentTaskId, normalized.operation(),
                accepted.size(), List.copyOf(accepted), trace
        );
    }

    private NormalizedBatch normalize(String tenant, String kbId, TaskDto.BatchDocumentsRequest request) {
        if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "batch request missing");
        String operation = required(request.operation(), "batch operation missing").toLowerCase(Locale.ROOT);
        if (!OPERATIONS.contains(operation)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported batch operation");
        }
        LinkedHashSet<String> docIds = new LinkedHashSet<>();
        for (String value : request.doc_ids() == null ? List.<String>of() : request.doc_ids()) {
            String docId = value == null ? "" : value.trim();
            if (!docId.isBlank()) docIds.add(docId);
        }
        if (docIds.isEmpty() || docIds.size() > 500) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "batch must contain 1 to 500 unique documents");
        }
        Map<String, Object> params = new LinkedHashMap<>();
        Map<String, Object> command = new LinkedHashMap<>();
        if (Set.of("chunk", "embed").contains(operation)) {
            String method = request.chunk_method() == null || request.chunk_method().isBlank()
                    ? "sentence" : request.chunk_method().trim().toLowerCase(Locale.ROOT);
            if (!CHUNK_METHODS.contains(method)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported chunk method");
            }
            int size = request.chunk_size() == null ? 500 : request.chunk_size();
            int overlap = request.chunk_overlap() == null ? 50 : request.chunk_overlap();
            String delimiter = request.chunk_delimiter() == null ? "" : request.chunk_delimiter();
            if (overlap >= size) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_overlap must be smaller than chunk_size");
            if ("delimiter".equals(method) && delimiter.isBlank()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_delimiter is required for delimiter method");
            }
            command.put("chunk_method", method);
            command.put("chunk_size", size);
            command.put("chunk_overlap", overlap);
            command.put("chunk_delimiter", delimiter);
            command.put("auto_summary", false);
        }
        if ("move".equals(operation)) {
            String target = required(request.target_kb_id(), "target_kb_id is required");
            if (kbId.equals(target)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "target knowledge base must differ from source");
            requireWritableKb(tenant, target);
            command.put("target_kb_id", target);
        }
        if ("tag".equals(operation)) {
            LinkedHashSet<String> tags = new LinkedHashSet<>();
            for (String value : request.tags() == null ? List.<String>of() : request.tags()) {
                String tag = value == null ? "" : value.trim();
                if (!tag.isBlank()) tags.add(tag);
            }
            if (tags.isEmpty()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "tags is required");
            String mode = request.mode() == null || request.mode().isBlank() ? "add" : request.mode().trim().toLowerCase(Locale.ROOT);
            if (!Set.of("add", "replace").contains(mode)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "tag mode must be add or replace");
            }
            command.put("tags", List.copyOf(tags));
            command.put("tag_mode", mode);
            params.put("mode", mode);
        }
        params.putAll(command);
        String batchName = request.batch_name() == null || request.batch_name().isBlank()
                ? "批量" + operation + "-" + LocalDateTime.now().format(BATCH_TIME)
                : request.batch_name().trim();
        return new NormalizedBatch(operation, List.copyOf(docIds), batchName, params, command);
    }

    @SuppressWarnings("unchecked")
    private Map<String, DocumentDescriptor> preflight(String tenant, String kbId, List<String> docIds) {
        Map<String, Object> response = executionClient.describeDocuments(tenant, kbId, docIds);
        List<String> missing = stringList(response.get("missing_doc_ids"));
        if (!missing.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.NOT_FOUND,
                    "documents not found: " + String.join(", ", missing.subList(0, Math.min(10, missing.size())))
            );
        }
        Map<String, DocumentDescriptor> result = new LinkedHashMap<>();
        Object rawItems = response.get("items");
        if (rawItems instanceof List<?> items) {
            for (Object raw : items) {
                if (!(raw instanceof Map<?, ?> item)) continue;
                String docId = stringValue(item.get("doc_id"));
                if (docId.isBlank()) continue;
                result.put(docId, new DocumentDescriptor(
                        docId, stringValue(item.get("filename")), valueAsInt(item.get("chunk_count"), 0)
                ));
            }
        }
        List<String> absent = docIds.stream().filter(docId -> !result.containsKey(docId)).toList();
        if (!absent.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution descriptor omitted requested documents");
        }
        return result;
    }

    private String taskType(String operation, int chunkCount) {
        return switch (operation) {
            case "chunk" -> chunkCount > 0 ? "DOC_RECHUNK" : "DOC_CHUNK";
            case "embed" -> "DOC_REINDEX";
            case "summarize" -> "DOC_SUMMARIZE";
            case "move" -> "DOC_MOVE";
            case "tag" -> "DOC_TAG";
            case "delete" -> "DOC_DELETE";
            default -> throw new IllegalArgumentException("unsupported operation");
        };
    }

    private void appendCreated(TaskEntity task) {
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(task.getTaskId());
        event.setTenantId(task.getTenantId());
        event.setKbId(task.getKbId());
        event.setDocId(task.getDocId());
        event.setEventKey("task:" + task.getTaskId() + ":created");
        event.setEventType("TASK_CREATED");
        event.setStage(task.getStage());
        event.setPayload(Map.of(
                "task_type", task.getTaskType(), "authority", "CONTROL",
                "batch_id", task.getBatchId(), "batch_is_parent", task.isBatchIsParent()
        ));
        eventRepository.save(event);
    }

    private KnowledgeBaseEntity requireWritableKb(String tenant, String kbId) {
        KnowledgeBaseEntity kb = knowledgeBaseRepository.findByTenantIdAndKbId(tenant, kbId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found"));
        if (kb.isFrozen() || !"ACTIVE".equalsIgnoreCase(kb.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "knowledge base is not writable");
        }
        return kb;
    }

    private TaskDto.TaskBatchView batchView(TaskBatchEntity batch) {
        return new TaskDto.TaskBatchView(
                batch.getBatchId(), batch.getTenantId(), batch.getKbId(), batch.getOperation(), batch.getBatchName(),
                batch.getParams(), batch.getParentTaskId(), batch.getTotalItems(), batch.getSucceededItems(),
                batch.getFailedItems(), batch.getCancelledItems(), batch.getRunningItems(), batch.getStatus(),
                batch.getCreatedBy(), batch.getCreatedAt(), batch.getUpdatedAt()
        );
    }

    private TaskDto.TaskView taskView(TaskEntity task) {
        return new TaskDto.TaskView(
                task.getTaskId(), task.getTenantId(), task.getKbId(), task.getDocId(), task.getTaskType(),
                task.getStatus(), task.getStage(), task.getFilename(), task.getBatchId(), task.getBatchName(),
                task.isBatchIsParent(), task.getProgressCurrent(), task.getProgressTotal(), task.getProgressPct(),
                task.isCancelRequested(), task.getErrorMessage(), task.getStateOwner(), task.getCreatedAt(), task.getUpdatedAt()
        );
    }

    private String writeJson(Map<String, Object> payload) {
        try { return objectMapper.writeValueAsString(payload); }
        catch (JsonProcessingException exception) { throw new IllegalStateException("batch command serialization failed", exception); }
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }

    private String stringValue(Object value) { return value == null ? "" : String.valueOf(value).trim(); }
    private Integer intValue(Object value) { return value == null ? null : valueAsInt(value, 0); }
    private int valueAsInt(Object value, int fallback) {
        if (value instanceof Number number) return number.intValue();
        try { return value == null || String.valueOf(value).isBlank() ? fallback : Integer.parseInt(String.valueOf(value)); }
        catch (NumberFormatException ignored) { return fallback; }
    }
    private List<String> stringList(Object value) {
        if (!(value instanceof List<?> list)) return List.of();
        return list.stream().map(this::stringValue).filter(item -> !item.isBlank()).toList();
    }

    private record DocumentDescriptor(String docId, String filename, int chunkCount) {}
    private record NormalizedBatch(
            String operation, List<String> docIds, String batchName,
            Map<String, Object> params, Map<String, Object> commandParameters
    ) {}
}
