package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
public class RunService {
    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private static final Map<String, Set<String>> TRANSITIONS = Map.of(
            "PENDING", Set.of("PENDING", "RUNNING", "CANCELLED"),
            "RUNNING", Set.of("RUNNING", "WAITING_APPROVAL", "SUCCEEDED", "FAILED", "CANCELLED"),
            "WAITING_APPROVAL", Set.of("WAITING_APPROVAL", "PENDING", "CANCELLED")
    );

    private final ConversationRepository conversationRepository;
    private final MessageRepository messageRepository;
    private final RunRepository runRepository;
    private final RunEventRepository eventRepository;
    private final RunNodeExecutionRepository nodeExecutionRepository;
    private final RunNodeAttemptRepository nodeAttemptRepository;
    private final WorkflowService workflowService;
    private final RunEventStream eventStream;
    private final ObjectMapper objectMapper;
    private final RunDispatchOutboxService dispatchOutboxService;
    private final RunExecutionLeaseService leaseService;
    private final RunDeadLetterService deadLetterService;
    private final RunApprovalService approvalService;

    public RunService(
            ConversationRepository conversationRepository,
            MessageRepository messageRepository,
            RunRepository runRepository,
            RunEventRepository eventRepository,
            RunNodeExecutionRepository nodeExecutionRepository,
            RunNodeAttemptRepository nodeAttemptRepository,
            WorkflowService workflowService,
            RunEventStream eventStream,
            ObjectMapper objectMapper,
            RunDispatchOutboxService dispatchOutboxService,
            RunExecutionLeaseService leaseService,
            RunDeadLetterService deadLetterService,
            RunApprovalService approvalService
    ) {
        this.conversationRepository = conversationRepository;
        this.messageRepository = messageRepository;
        this.runRepository = runRepository;
        this.eventRepository = eventRepository;
        this.nodeExecutionRepository = nodeExecutionRepository;
        this.nodeAttemptRepository = nodeAttemptRepository;
        this.workflowService = workflowService;
        this.eventStream = eventStream;
        this.objectMapper = objectMapper;
        this.dispatchOutboxService = dispatchOutboxService;
        this.leaseService = leaseService;
        this.deadLetterService = deadLetterService;
        this.approvalService = approvalService;
    }

    @Transactional
    public RunDto.ConversationView createConversation(String tenantId, String actorId, String title) {
        ConversationEntity entity = new ConversationEntity();
        entity.setTenantId(required(tenantId, "tenant id missing"));
        entity.setCreatedBy(required(actorId, "actor id missing"));
        entity.setTitle(title == null || title.isBlank() ? "新对话" : title.trim());
        return conversationView(conversationRepository.save(entity));
    }

    @Transactional(readOnly = true)
    public List<RunDto.ConversationView> listConversations(String tenantId, String actorId) {
        return conversationRepository.findByTenantIdAndCreatedByOrderByUpdatedAtDesc(tenantId, actorId).stream()
                .map(this::conversationView).toList();
    }

    @Transactional(readOnly = true)
    public List<RunDto.MessageView> listMessages(String tenantId, String actorId, String conversationId) {
        requireConversation(tenantId, actorId, conversationId);
        return messageRepository.findByConversationIdAndTenantIdOrderByCreatedAtAsc(conversationId, tenantId).stream()
                .map(this::messageView).toList();
    }

    @Transactional
    public RunDto.MessageRunResponse createMessageRun(
            String tenantId,
            String actorId,
            String conversationId,
            JsonNode content,
            String workflowId,
            int workflowVersionNo
    ) {
        ConversationEntity conversation = requireConversation(tenantId, actorId, conversationId);
        if (!"ACTIVE".equals(conversation.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "conversation is not active");
        }
        WorkflowDto.VersionView workflowVersion = workflowService.getVersion(tenantId, workflowId, workflowVersionNo);

        RunEntity run = new RunEntity();
        run.setTenantId(tenantId);
        run.setConversationId(conversationId);
        run.setWorkflowId(workflowId);
        run.setWorkflowVersionId(workflowVersion.version_id());
        run.setWorkflowVersionNo(workflowVersion.version_no());
        run.setWorkflowChecksum(workflowVersion.checksum());
        run.setInputPayloadJson(writeJson(content));
        run.setCreatedBy(actorId);
        run = runRepository.saveAndFlush(run);

        MessageEntity message = new MessageEntity();
        message.setTenantId(tenantId);
        message.setConversationId(conversationId);
        message.setRole("USER");
        message.setContentJson(writeJson(content));
        message.setRelatedRunId(run.getRunId());
        message = messageRepository.save(message);
        conversationRepository.save(conversation);

        RunEventEntity created = newEvent(
                tenantId, run, "control:" + run.getRunId() + ":created", "RUN_CREATED",
                "PENDING", "QUEUED", 0, JsonNodeFactory.instance.objectNode(), "control-plane"
        );
        created.setSequenceNo(1);
        created = eventRepository.save(created);
        dispatchOutboxService.enqueue(tenantId, run.getRunId());
        RunDto.EventView eventView = eventView(created);
        eventStream.publish(eventView);
        return new RunDto.MessageRunResponse("ok", messageView(message), runView(run));
    }

    @Transactional
    public RunDto.MessageRunResponse createPublishedApiRun(
            String tenantId,
            String ownerUserId,
            JsonNode content,
            String workflowId,
            int workflowVersionNo
    ) {
        RunDto.ConversationView conversation = createConversation(tenantId, ownerUserId, "API 调用");
        return createMessageRun(
                tenantId, ownerUserId, conversation.conversation_id(), content, workflowId, workflowVersionNo
        );
    }

    @Transactional(readOnly = true)
    public RunDto.RunView getRun(String tenantId, String actorId, String runId) {
        return runView(requireUserRun(tenantId, actorId, runId));
    }

    @Transactional(readOnly = true)
    public List<RunDto.EventView> listEvents(String tenantId, String actorId, String runId) {
        requireUserRun(tenantId, actorId, runId);
        return eventRepository.findByRunIdAndTenantIdOrderBySequenceNoAsc(runId, tenantId).stream()
                .map(this::eventView).toList();
    }

    @Transactional
    public RunDto.EventView cancel(String tenantId, String actorId, String runId) {
        RunEntity run = runRepository.lockByRunIdAndTenantId(runId, tenantId)
                .filter(item -> actorId.equals(item.getCreatedBy()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        if (TERMINAL.contains(run.getStatus())) {
            if ("CANCELLED".equals(run.getStatus())) {
                return eventRepository.findByRunIdAndTenantIdOrderBySequenceNoAsc(runId, tenantId).stream()
                        .filter(event -> "RUN_CANCELLED".equals(event.getEventType())).findFirst()
                        .map(this::eventView)
                        .orElseThrow();
            }
            throw new ResponseStatusException(HttpStatus.CONFLICT, "terminal run cannot be cancelled");
        }
        applyStatus(run, "CANCELLED", "CANCELLED", run.getProgress());
        runRepository.save(run);
        RunEventEntity event = newEvent(
                tenantId, run, "control:" + runId + ":cancel", "RUN_CANCELLED", "CANCELLED",
                "CANCELLED", run.getProgress(), JsonNodeFactory.instance.objectNode(), "control-plane"
        );
        event.setSequenceNo(eventRepository.maxSequence(runId, tenantId) + 1);
        RunDto.EventView view = eventView(eventRepository.save(event));
        dispatchOutboxService.cancelForRun(runId);
        approvalService.cancelPendingForRun(tenantId, runId);
        leaseService.releaseForTerminal(runId);
        eventStream.publish(view);
        return view;
    }

    @Transactional
    public AppendResult appendInternalEvent(String runId, RunDto.InternalEventRequest request) {
        RunEventEntity duplicate = eventRepository.findByTenantIdAndExternalEventId(request.tenant_id(), request.event_id()).orElse(null);
        if (duplicate != null) {
            if (!runId.equals(duplicate.getRunId())) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "event id already belongs to another run");
            }
            return new AppendResult(eventView(duplicate), true);
        }

        RunEntity run = runRepository.lockByRunIdAndTenantId(runId, request.tenant_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        String nextStatus = blankTo(request.status(), run.getStatus());
        String nextStage = blankTo(request.stage(), run.getStage());
        int nextProgress = request.progress() == null ? run.getProgress() : request.progress();
        if (nextProgress < run.getProgress()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "run progress cannot decrease");
        }
        assertTransition(run.getStatus(), nextStatus);
        applyStatus(run, nextStatus, nextStage, nextProgress);
        if (request.output_payload() != null) run.setOutputPayloadJson(writeJson(request.output_payload()));
        if (request.citations() != null) run.setCitationsJson(writeJson(request.citations()));
        if (request.metrics() != null) run.setMetricsJson(writeJson(request.metrics()));
        run.setErrorCode(blankTo(request.error_code(), ""));
        run.setErrorMessage(blankTo(request.error_message(), ""));
        runRepository.save(run);

        RunEventEntity event = newEvent(
                request.tenant_id(), run, request.event_id(), request.event_type(), nextStatus, nextStage,
                nextProgress, request.payload() == null ? JsonNodeFactory.instance.objectNode() : request.payload(),
                request.source_service()
        );
        event.setSequenceNo(eventRepository.maxSequence(runId, request.tenant_id()) + 1);
        event = eventRepository.save(event);
        syncNodeExecution(event, request.payload());

        if ("SUCCEEDED".equals(nextStatus) && request.output_payload() != null) {
            createAssistantMessageOnce(run, request.output_payload());
        }
        if (TERMINAL.contains(nextStatus)) {
            leaseService.releaseForTerminal(runId);
            deadLetterService.completeForRun(runId, nextStatus);
        }
        RunDto.EventView view = eventView(event);
        eventStream.publish(view);
        return new AppendResult(view, false);
    }

    @Transactional
    public void deleteConversation(String tenantId, String actorId, String conversationId) {
        ConversationEntity conversation = requireConversation(tenantId, actorId, conversationId);
        List<RunEntity> runs = runRepository.findByConversationIdAndTenantId(conversationId, tenantId);
        if (runs.stream().anyMatch(run -> !TERMINAL.contains(run.getStatus()))) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "conversation has active runs");
        }
        for (RunEntity run : runs) {
            eventRepository.deleteByRunIdAndTenantId(run.getRunId(), tenantId);
            dispatchOutboxService.deleteForRun(run.getRunId());
            leaseService.deleteForRun(run.getRunId());
        }
        messageRepository.deleteByConversationIdAndTenantId(conversationId, tenantId);
        runRepository.deleteAll(runs);
        conversationRepository.delete(conversation);
    }

    @Transactional(readOnly = true)
    public RunDto.RunView internalGetRun(String tenantId, String runId) {
        return runRepository.findByRunIdAndTenantId(runId, tenantId).map(this::runView)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
    }

    @Transactional(readOnly = true)
    public RunDto.RunView adminGetRun(String tenantId, String runId) {
        return internalGetRun(tenantId, runId);
    }

    @Transactional(readOnly = true)
    public List<RunDto.RunView> adminListRuns(String tenantId, String status) {
        List<RunEntity> runs;
        if (status == null || status.isBlank()) {
            runs = runRepository.findTop100ByTenantIdOrderByCreatedAtDesc(tenantId);
        } else {
            String normalized = status.trim().toUpperCase();
            if (!Set.of("PENDING", "RUNNING", "WAITING_APPROVAL", "SUCCEEDED", "FAILED", "CANCELLED").contains(normalized)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid run status");
            }
            runs = runRepository.findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(tenantId, normalized);
        }
        return runs.stream().map(this::runView).toList();
    }

    @Transactional(readOnly = true)
    public List<RunDto.EventView> adminListEvents(String tenantId, String runId) {
        runRepository.findByRunIdAndTenantId(runId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        return eventRepository.findByRunIdAndTenantIdOrderBySequenceNoAsc(runId, tenantId).stream()
                .map(this::eventView)
                .toList();
    }

    @Transactional(readOnly = true)
    public List<RunDto.NodeExecutionView> adminListNodeExecutions(String tenantId, String runId) {
        runRepository.findByRunIdAndTenantId(runId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        Map<String, List<RunNodeAttemptEntity>> attemptsByNode = nodeAttemptRepository
                .findByTenantIdAndRunIdOrderByNodeIdAscAttemptNoAsc(tenantId, runId).stream()
                .collect(Collectors.groupingBy(RunNodeAttemptEntity::getNodeId));
        return nodeExecutionRepository.findByTenantIdAndRunIdOrderByOrdinalAsc(tenantId, runId).stream()
                .map(node -> nodeExecutionView(node, attemptsByNode.getOrDefault(node.getNodeId(), List.of())))
                .toList();
    }

    @Transactional(readOnly = true)
    public RunDto.ExecutionContextView internalExecutionContext(String runId) {
        RunEntity run = runRepository.findById(runId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        return executionContext(run);
    }

    private RunDto.ExecutionContextView executionContext(RunEntity run) {
        WorkflowDto.VersionView version = workflowService.getVersion(
                run.getTenantId(), run.getWorkflowId(), run.getWorkflowVersionNo()
        );
        if (!run.getWorkflowVersionId().equals(version.version_id()) || !run.getWorkflowChecksum().equals(version.checksum())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "run workflow snapshot no longer matches published version");
        }
        return new RunDto.ExecutionContextView(
                run.getRunId(), run.getTenantId(), run.getCreatedBy(), run.getConversationId(), run.getStatus(),
                run.getWorkflowId(), run.getWorkflowVersionId(), run.getWorkflowVersionNo(), run.getWorkflowChecksum(),
                version.dsl(), version.layout(), readJson(run.getInputPayloadJson()),
                approvalService.resumeContext(run.getTenantId(), run.getRunId())
        );
    }

    private void createAssistantMessageOnce(RunEntity run, JsonNode output) {
        if (messageRepository.findByRelatedRunIdAndRole(run.getRunId(), "ASSISTANT").isPresent()) return;
        MessageEntity message = new MessageEntity();
        message.setTenantId(run.getTenantId());
        message.setConversationId(run.getConversationId());
        message.setRole("ASSISTANT");
        message.setContentJson(writeJson(output));
        message.setRelatedRunId(run.getRunId());
        messageRepository.save(message);
    }

    private void assertTransition(String current, String next) {
        if (TERMINAL.contains(current)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "terminal run cannot accept new events");
        }
        if (!TRANSITIONS.getOrDefault(current, Set.of()).contains(next)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "invalid run status transition: " + current + " -> " + next);
        }
    }

    private void applyStatus(RunEntity run, String status, String stage, int progress) {
        Instant now = Instant.now();
        run.setStatus(status);
        run.setStage(stage);
        run.setProgress(progress);
        if ("RUNNING".equals(status) && run.getStartedAt() == null) run.setStartedAt(now);
        if (TERMINAL.contains(status)) {
            run.setFinishedAt(now);
            if ("SUCCEEDED".equals(status)) run.setProgress(100);
        }
    }

    private ConversationEntity requireConversation(String tenantId, String actorId, String conversationId) {
        return conversationRepository.findByConversationIdAndTenantIdAndCreatedBy(conversationId, tenantId, actorId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "conversation not found"));
    }

    private RunEntity requireUserRun(String tenantId, String actorId, String runId) {
        return runRepository.findByRunIdAndTenantIdAndCreatedBy(runId, tenantId, actorId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
    }

    private RunEventEntity newEvent(
            String tenantId, RunEntity run, String externalId, String eventType, String status,
            String stage, int progress, JsonNode payload, String sourceService
    ) {
        RunEventEntity event = new RunEventEntity();
        event.setTenantId(tenantId);
        event.setRunId(run.getRunId());
        event.setExternalEventId(externalId);
        event.setEventType(eventType);
        event.setStatus(status);
        event.setStage(stage);
        event.setProgress(progress);
        event.setPayloadJson(writeJson(payload));
        event.setSourceService(required(sourceService, "source service missing"));
        return event;
    }

    public RunDto.ConversationView conversationView(ConversationEntity e) {
        return new RunDto.ConversationView(e.getConversationId(), e.getTenantId(), e.getTitle(), e.getStatus(), e.getCreatedBy(), e.getCreatedAt(), e.getUpdatedAt());
    }
    public RunDto.MessageView messageView(MessageEntity e) {
        return new RunDto.MessageView(e.getMessageId(), e.getTenantId(), e.getConversationId(), e.getRole(), readJson(e.getContentJson()), e.getRelatedRunId(), e.getCreatedAt());
    }
    public RunDto.RunView runView(RunEntity e) {
        return new RunDto.RunView(
                e.getRunId(), e.getTenantId(), e.getConversationId(), e.getWorkflowId(), e.getWorkflowVersionId(),
                e.getWorkflowVersionNo(), e.getWorkflowChecksum(), e.getStatus(), e.getStage(), e.getProgress(),
                readJson(e.getInputPayloadJson()), readJson(e.getOutputPayloadJson()), readJson(e.getCitationsJson()),
                readJson(e.getMetricsJson()), e.getErrorCode(), e.getErrorMessage(), e.getCreatedBy(), e.getStartedAt(),
                e.getFinishedAt(), e.getCreatedAt(), e.getUpdatedAt(), e.getRowVersion()
        );
    }
    public RunDto.EventView eventView(RunEventEntity e) {
        return new RunDto.EventView(
                e.getEventId(), e.getExternalEventId(), e.getTenantId(), e.getRunId(), e.getSequenceNo(),
                e.getEventType(), e.getStatus(), e.getStage(), e.getProgress(), readJson(e.getPayloadJson()),
                e.getSourceService(), e.getCreatedAt()
        );
    }

    public RunDto.NodeExecutionView nodeExecutionView(RunNodeExecutionEntity e, List<RunNodeAttemptEntity> attempts) {
        return new RunDto.NodeExecutionView(
                e.getNodeExecutionId(), e.getTenantId(), e.getRunId(), e.getNodeId(), e.getNodeType(),
                e.getOrdinal(), e.getStatus(), e.getStartedAt(), e.getCompletedAt(), e.getDurationMs(),
                readJson(e.getDetailsJson()), e.getErrorMessage(), attempts.stream().map(this::nodeAttemptView).toList(),
                e.getCreatedAt(), e.getUpdatedAt()
        );
    }

    private RunDto.NodeAttemptView nodeAttemptView(RunNodeAttemptEntity e) {
        return new RunDto.NodeAttemptView(
                e.getNodeAttemptId(), e.getAttemptNo(), e.getStatus(), e.getStartedAt(), e.getCompletedAt(),
                e.getDurationMs(), e.isRetryable(), e.getRetryDelayMs(), e.getErrorType(), e.getErrorMessage(),
                readJson(e.getDetailsJson())
        );
    }

    private void syncNodeExecution(RunEventEntity event, JsonNode payload) {
        Set<String> nodeEvents = Set.of("NODE_STARTED", "NODE_WAITING_APPROVAL", "NODE_COMPLETED", "NODE_FAILED");
        Set<String> attemptEvents = Set.of("NODE_ATTEMPT_STARTED", "NODE_ATTEMPT_SUCCEEDED", "NODE_ATTEMPT_FAILED");
        if (!nodeEvents.contains(event.getEventType()) && !attemptEvents.contains(event.getEventType())) return;
        JsonNode safePayload = payload == null ? JsonNodeFactory.instance.objectNode() : payload;
        String nodeId = safePayload.path("node_id").asText("").trim();
        if (nodeId.isBlank()) return;
        RunNodeExecutionEntity node = nodeExecutionRepository
                .findByTenantIdAndRunIdAndNodeId(event.getTenantId(), event.getRunId(), nodeId)
                .orElseGet(RunNodeExecutionEntity::new);
        node.setTenantId(event.getTenantId());
        node.setRunId(event.getRunId());
        node.setNodeId(nodeId);
        node.setNodeType(safePayload.path("node_type").asText("unknown"));
        node.setOrdinal(safePayload.path("ordinal").asInt(0));
        if (node.getStartedAt() == null) node.setStartedAt(event.getCreatedAt());
        if (node.getStatus() == null || node.getStatus().isBlank()) node.setStatus("RUNNING");
        if ("NODE_STARTED".equals(event.getEventType())) {
            node.setStatus("RUNNING");
        } else if ("NODE_WAITING_APPROVAL".equals(event.getEventType())) {
            node.setStatus("WAITING_APPROVAL");
            JsonNode details = safePayload.path("details");
            node.setDetailsJson(writeJson(details.isMissingNode() ? JsonNodeFactory.instance.objectNode() : details));
        } else if (Set.of("NODE_COMPLETED", "NODE_FAILED").contains(event.getEventType())) {
            node.setStatus("NODE_COMPLETED".equals(event.getEventType()) ? "SUCCEEDED" : "FAILED");
            node.setCompletedAt(event.getCreatedAt());
            node.setDurationMs(Math.max(0, Duration.between(node.getStartedAt(), event.getCreatedAt()).toMillis()));
            JsonNode details = safePayload.path("details");
            node.setDetailsJson(writeJson(details.isMissingNode() ? JsonNodeFactory.instance.objectNode() : details));
            String errorMessage = details.path("error_message").asText("");
            node.setErrorMessage("NODE_FAILED".equals(event.getEventType())
                    ? errorMessage.substring(0, Math.min(2000, errorMessage.length()))
                    : "");
        }
        node.setUpdatedAt(event.getCreatedAt());
        node = nodeExecutionRepository.save(node);
        if (attemptEvents.contains(event.getEventType())) syncNodeAttempt(event, safePayload, node);
    }

    private void syncNodeAttempt(RunEventEntity event, JsonNode payload, RunNodeExecutionEntity node) {
        JsonNode details = payload.path("details");
        int attemptNo = details.path("attempt_no").asInt(0);
        if (attemptNo < 1 || attemptNo > 5) return;
        RunNodeAttemptEntity attempt = nodeAttemptRepository
                .findByTenantIdAndRunIdAndNodeIdAndAttemptNo(event.getTenantId(), event.getRunId(), node.getNodeId(), attemptNo)
                .orElseGet(RunNodeAttemptEntity::new);
        attempt.setNodeExecutionId(node.getNodeExecutionId());
        attempt.setTenantId(event.getTenantId());
        attempt.setRunId(event.getRunId());
        attempt.setNodeId(node.getNodeId());
        attempt.setAttemptNo(attemptNo);
        if (attempt.getStartedAt() == null) attempt.setStartedAt(event.getCreatedAt());
        if ("NODE_ATTEMPT_STARTED".equals(event.getEventType())) {
            attempt.setStatus("RUNNING");
        } else {
            attempt.setStatus("NODE_ATTEMPT_SUCCEEDED".equals(event.getEventType()) ? "SUCCEEDED" : "FAILED");
            attempt.setCompletedAt(event.getCreatedAt());
            attempt.setDurationMs(Math.max(0, Duration.between(attempt.getStartedAt(), event.getCreatedAt()).toMillis()));
            attempt.setRetryable(details.path("retryable").asBoolean(false));
            attempt.setRetryDelayMs(Math.max(0, Math.min(60000, details.path("retry_delay_ms").asLong(0))));
            attempt.setErrorType(truncate(details.path("error_type").asText(""), 256));
            attempt.setErrorMessage(truncate(details.path("error_message").asText(""), 2000));
        }
        attempt.setDetailsJson(writeJson(details.isMissingNode() ? JsonNodeFactory.instance.objectNode() : details));
        attempt.setUpdatedAt(event.getCreatedAt());
        nodeAttemptRepository.save(attempt);
    }

    private String truncate(String value, int maxLength) {
        String safe = value == null ? "" : value;
        return safe.substring(0, Math.min(maxLength, safe.length()));
    }

    private String writeJson(JsonNode value) {
        try { return objectMapper.writeValueAsString(value == null ? JsonNodeFactory.instance.objectNode() : value); }
        catch (JsonProcessingException ex) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid JSON payload", ex); }
    }
    private JsonNode readJson(String value) {
        try { return objectMapper.readTree(value == null || value.isBlank() ? "{}" : value); }
        catch (JsonProcessingException ex) { throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "stored run JSON is invalid", ex); }
    }
    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }
    private String blankTo(String value, String fallback) { return value == null || value.isBlank() ? fallback : value.trim(); }

    public record AppendResult(RunDto.EventView event, boolean duplicate) {
    }
}
