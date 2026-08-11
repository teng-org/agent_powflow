package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Optional;
import java.util.ArrayList;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RunServiceTest {
    private final ObjectMapper objectMapper = new ObjectMapper();
    private ConversationRepository conversationRepository;
    private MessageRepository messageRepository;
    private RunRepository runRepository;
    private RunEventRepository eventRepository;
    private RunNodeExecutionRepository nodeExecutionRepository;
    private RunNodeAttemptRepository nodeAttemptRepository;
    private RunEventStream eventStream;
    private WorkflowService workflowService;
    private RunDispatchOutboxService dispatchOutboxService;
    private RunExecutionLeaseService leaseService;
    private RunDeadLetterService deadLetterService;
    private RunApprovalService approvalService;
    private RunService service;

    @BeforeEach
    void setUp() {
        conversationRepository = mock(ConversationRepository.class);
        messageRepository = mock(MessageRepository.class);
        runRepository = mock(RunRepository.class);
        eventRepository = mock(RunEventRepository.class);
        nodeExecutionRepository = mock(RunNodeExecutionRepository.class);
        nodeAttemptRepository = mock(RunNodeAttemptRepository.class);
        eventStream = mock(RunEventStream.class);
        workflowService = mock(WorkflowService.class);
        dispatchOutboxService = mock(RunDispatchOutboxService.class);
        leaseService = mock(RunExecutionLeaseService.class);
        deadLetterService = mock(RunDeadLetterService.class);
        approvalService = mock(RunApprovalService.class);
        service = new RunService(
                conversationRepository, messageRepository, runRepository, eventRepository, nodeExecutionRepository, nodeAttemptRepository,
                workflowService, eventStream, objectMapper, dispatchOutboxService, leaseService, deadLetterService, approvalService
        );
        when(runRepository.save(any(RunEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(eventRepository.save(any(RunEventEntity.class))).thenAnswer(invocation -> {
            RunEventEntity event = invocation.getArgument(0);
            event.prePersist();
            return event;
        });
        when(messageRepository.save(any(MessageEntity.class))).thenAnswer(invocation -> {
            MessageEntity message = invocation.getArgument(0);
            message.prePersist();
            return message;
        });
    }

    @Test
    void acceptsRunningThenSucceededAndCreatesAssistantMessage() throws Exception {
        RunEntity run = pendingRun();
        when(eventRepository.findByTenantIdAndExternalEventId(any(), any())).thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(1L, 2L);
        when(messageRepository.findByRelatedRunIdAndRole("run-1", "ASSISTANT")).thenReturn(Optional.empty());

        RunService.AppendResult running = service.appendInternalEvent("run-1", event("evt-1", "RUNNING", 20, null));
        RunService.AppendResult succeeded = service.appendInternalEvent(
                "run-1", event("evt-2", "SUCCEEDED", 90, objectMapper.readTree("{\"answer\":\"done\"}"))
        );

        assertFalse(running.duplicate());
        assertEquals("RUNNING", running.event().status());
        assertEquals("SUCCEEDED", succeeded.event().status());
        assertEquals(100, run.getProgress());
        assertTrue(run.getStartedAt() != null && run.getFinishedAt() != null);
        verify(messageRepository).save(any(MessageEntity.class));
        verify(eventStream).publish(running.event());
        verify(eventStream).publish(succeeded.event());
    }

    @Test
    void rejectsSkippingRunningState() throws Exception {
        RunEntity run = pendingRun();
        when(eventRepository.findByTenantIdAndExternalEventId(any(), any())).thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.appendInternalEvent("run-1", event("evt-1", "SUCCEEDED", 100, objectMapper.createObjectNode()))
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        verify(eventRepository, never()).save(any());
    }

    @Test
    void progressCannotMoveBackwards() {
        RunEntity run = pendingRun();
        run.setStatus("RUNNING");
        run.setProgress(60);
        when(eventRepository.findByTenantIdAndExternalEventId(any(), any())).thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.appendInternalEvent("run-1", event("evt-1", "RUNNING", 40, null))
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
    }

    @Test
    void duplicateExternalEventIsIdempotent() {
        RunEventEntity existing = existingEvent();
        when(eventRepository.findByTenantIdAndExternalEventId("tenant-a", "evt-1")).thenReturn(Optional.of(existing));

        RunService.AppendResult result = service.appendInternalEvent("run-1", event("evt-1", "RUNNING", 20, null));

        assertTrue(result.duplicate());
        assertEquals(existing.getEventId(), result.event().event_id());
        verify(runRepository, never()).lockByRunIdAndTenantId(any(), any());
    }

    @Test
    void activeRunPreventsConversationDeletion() {
        ConversationEntity conversation = new ConversationEntity();
        conversation.setConversationId("conv-1");
        conversation.setTenantId("tenant-a");
        conversation.setCreatedBy("user-a");
        conversation.setTitle("test");
        conversation.prePersist();
        when(conversationRepository.findByConversationIdAndTenantIdAndCreatedBy("conv-1", "tenant-a", "user-a"))
                .thenReturn(Optional.of(conversation));
        when(runRepository.findByConversationIdAndTenantId("conv-1", "tenant-a")).thenReturn(List.of(pendingRun()));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.deleteConversation("tenant-a", "user-a", "conv-1")
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        verify(conversationRepository, never()).delete(any());
    }

    @Test
    void pendingExecutionContextIncludesImmutableWorkflowSnapshot() throws Exception {
        RunEntity run = pendingRun();
        when(workflowService.getVersion("tenant-a", "wf-1", 1)).thenReturn(version("a".repeat(64)));

        when(runRepository.findById("run-1")).thenReturn(Optional.of(run));
        RunDto.ExecutionContextView context = service.internalExecutionContext("run-1");

        assertEquals("run-1", context.run_id());
        assertEquals("PENDING", context.status());
        assertEquals("llm.generate", context.workflow_dsl().path("nodes").get(1).path("type").asText());
        assertEquals("a".repeat(64), context.workflow_checksum());
    }

    @Test
    void executionContextRejectsChangedPublishedSnapshot() {
        RunEntity run = pendingRun();
        when(runRepository.findById("run-1")).thenReturn(Optional.of(run));
        when(workflowService.getVersion("tenant-a", "wf-1", 1)).thenReturn(version("b".repeat(64)));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.internalExecutionContext("run-1")
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
    }

    @Test
    void tenantAdminCanReadRunAndOrderedEventsWithoutBeingTheCreator() {
        RunEntity run = pendingRun();
        RunEventEntity event = existingEvent();
        when(runRepository.findByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.findByRunIdAndTenantIdOrderBySequenceNoAsc("run-1", "tenant-a"))
                .thenReturn(List.of(event));
        when(runRepository.findTop100ByTenantIdAndStatusOrderByCreatedAtDesc("tenant-a", "PENDING"))
                .thenReturn(List.of(run));

        RunDto.RunView runView = service.adminGetRun("tenant-a", "run-1");
        List<RunDto.EventView> events = service.adminListEvents("tenant-a", "run-1");
        List<RunDto.RunView> runs = service.adminListRuns("tenant-a", "pending");

        assertEquals("run-1", runView.run_id());
        assertEquals(1, events.size());
        assertEquals(2, events.getFirst().sequence_no());
        assertEquals("run-1", runs.getFirst().run_id());
    }

    @Test
    void nodeEventsMaintainAuthoritativeNodeExecutionRecord() throws Exception {
        RunEntity run = pendingRun();
        AtomicReference<RunNodeExecutionEntity> stored = new AtomicReference<>();
        when(eventRepository.findByTenantIdAndExternalEventId(any(), any())).thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(1L, 2L);
        when(nodeExecutionRepository.findByTenantIdAndRunIdAndNodeId("tenant-a", "run-1", "search-1"))
                .thenAnswer(ignored -> Optional.ofNullable(stored.get()));
        when(nodeExecutionRepository.save(any(RunNodeExecutionEntity.class))).thenAnswer(invocation -> {
            RunNodeExecutionEntity node = invocation.getArgument(0);
            node.prePersist();
            stored.set(node);
            return node;
        });
        var startedPayload = objectMapper.readTree("{\"node_id\":\"search-1\",\"node_type\":\"retrieval.search\",\"ordinal\":1,\"details\":{}}");
        var completedPayload = objectMapper.readTree("{\"node_id\":\"search-1\",\"node_type\":\"retrieval.search\",\"ordinal\":1,\"details\":{\"result_count\":2}}");

        service.appendInternalEvent("run-1", new RunDto.InternalEventRequest(
                "node-start", "tenant-a", "execution-worker", "NODE_STARTED", "RUNNING", "node.retrieval.search", 20,
                startedPayload, null, null, null, "", ""
        ));
        service.appendInternalEvent("run-1", new RunDto.InternalEventRequest(
                "node-complete", "tenant-a", "execution-worker", "NODE_COMPLETED", "RUNNING", "node.retrieval.search", 40,
                completedPayload, null, null, null, "", ""
        ));

        assertEquals("SUCCEEDED", stored.get().getStatus());
        assertEquals(2, objectMapper.readTree(stored.get().getDetailsJson()).path("result_count").asInt());
        assertTrue(stored.get().getDurationMs() >= 0);
    }

    @Test
    void nodeAttemptEventsMaintainOrderedAttemptAudit() throws Exception {
        RunEntity run = pendingRun();
        AtomicReference<RunNodeExecutionEntity> storedNode = new AtomicReference<>();
        List<RunNodeAttemptEntity> attempts = new ArrayList<>();
        when(eventRepository.findByTenantIdAndExternalEventId(any(), any())).thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(runRepository.findByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(1L);
        when(nodeExecutionRepository.findByTenantIdAndRunIdAndNodeId("tenant-a", "run-1", "tool-1"))
                .thenAnswer(ignored -> Optional.ofNullable(storedNode.get()));
        when(nodeExecutionRepository.save(any(RunNodeExecutionEntity.class))).thenAnswer(invocation -> {
            RunNodeExecutionEntity node = invocation.getArgument(0);
            node.prePersist();
            storedNode.set(node);
            return node;
        });
        when(nodeAttemptRepository.findByTenantIdAndRunIdAndNodeIdAndAttemptNo(any(), any(), any(), any(Integer.class)))
                .thenAnswer(invocation -> attempts.stream()
                        .filter(item -> item.getAttemptNo() == (int) invocation.getArgument(3)).findFirst());
        when(nodeAttemptRepository.save(any(RunNodeAttemptEntity.class))).thenAnswer(invocation -> {
            RunNodeAttemptEntity attempt = invocation.getArgument(0);
            attempt.prePersist();
            if (attempts.stream().noneMatch(item -> item.getAttemptNo() == attempt.getAttemptNo())) attempts.add(attempt);
            return attempt;
        });

        appendNodeEvent("node-start", "NODE_STARTED", 20, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{}}");
        appendNodeEvent("attempt-1-start", "NODE_ATTEMPT_STARTED", 20, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{\"attempt_no\":1,\"max_attempts\":2}}");
        appendNodeEvent("attempt-1-fail", "NODE_ATTEMPT_FAILED", 20, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{\"attempt_no\":1,\"max_attempts\":2,\"retryable\":true,\"retry_delay_ms\":100,\"error_type\":\"TimeoutException\",\"error_message\":\"timed out\"}}");
        appendNodeEvent("attempt-2-start", "NODE_ATTEMPT_STARTED", 20, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{\"attempt_no\":2,\"max_attempts\":2}}");
        appendNodeEvent("attempt-2-ok", "NODE_ATTEMPT_SUCCEEDED", 20, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{\"attempt_no\":2,\"max_attempts\":2}}");
        appendNodeEvent("node-complete", "NODE_COMPLETED", 40, "{\"node_id\":\"tool-1\",\"node_type\":\"tool.http\",\"ordinal\":1,\"details\":{\"attempt_count\":2}}");

        attempts.sort(java.util.Comparator.comparingInt(RunNodeAttemptEntity::getAttemptNo));
        when(nodeExecutionRepository.findByTenantIdAndRunIdOrderByOrdinalAsc("tenant-a", "run-1"))
                .thenReturn(List.of(storedNode.get()));
        when(nodeAttemptRepository.findByTenantIdAndRunIdOrderByNodeIdAscAttemptNoAsc("tenant-a", "run-1"))
                .thenReturn(attempts);
        var view = service.adminListNodeExecutions("tenant-a", "run-1").getFirst();

        assertEquals(2, view.attempts().size());
        assertEquals("FAILED", view.attempts().getFirst().status());
        assertTrue(view.attempts().getFirst().retryable());
        assertEquals(100, view.attempts().getFirst().retry_delay_ms());
        assertEquals("SUCCEEDED", view.attempts().get(1).status());
        assertEquals("SUCCEEDED", view.status());
    }

    private void appendNodeEvent(String eventId, String eventType, int progress, String payloadJson) throws Exception {
        service.appendInternalEvent("run-1", new RunDto.InternalEventRequest(
                eventId, "tenant-a", "execution-worker", eventType, "RUNNING", "node.tool.http", progress,
                objectMapper.readTree(payloadJson), null, null, null, "", ""
        ));
    }

    private RunEntity pendingRun() {
        RunEntity run = new RunEntity();
        run.setRunId("run-1");
        run.setTenantId("tenant-a");
        run.setConversationId("conv-1");
        run.setWorkflowId("wf-1");
        run.setWorkflowVersionId("ver-1");
        run.setWorkflowVersionNo(1);
        run.setWorkflowChecksum("a".repeat(64));
        run.setCreatedBy("user-a");
        run.prePersist();
        return run;
    }

    private RunDto.InternalEventRequest event(String eventId, String status, int progress, com.fasterxml.jackson.databind.JsonNode output) {
        return new RunDto.InternalEventRequest(
                eventId, "tenant-a", "execution-plane", "RUN_PROGRESS", status, status, progress,
                objectMapper.createObjectNode(), output, null, null, "", ""
        );
    }

    private RunEventEntity existingEvent() {
        RunEventEntity event = new RunEventEntity();
        event.setExternalEventId("evt-1");
        event.setTenantId("tenant-a");
        event.setRunId("run-1");
        event.setSequenceNo(2);
        event.setEventType("RUN_PROGRESS");
        event.setStatus("RUNNING");
        event.setStage("RUNNING");
        event.setProgress(20);
        event.setPayloadJson("{}");
        event.setSourceService("execution-plane");
        event.prePersist();
        return event;
    }

    private WorkflowDto.VersionView version(String checksum) {
        try {
            return new WorkflowDto.VersionView(
                    "ver-1", "wf-1", "tenant-a", 1,
                    objectMapper.readTree("{\"entry_node_id\":\"start\",\"nodes\":[{\"id\":\"start\",\"type\":\"start\"},{\"id\":\"llm\",\"type\":\"llm.generate\"},{\"id\":\"end\",\"type\":\"end\"}],\"edges\":[]}"),
                    objectMapper.createObjectNode(), checksum, "user-a", java.time.Instant.now()
            );
        } catch (Exception ex) {
            throw new IllegalStateException(ex);
        }
    }
}
