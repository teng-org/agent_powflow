package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.approvals.ApprovalAlertService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RunApprovalServiceTest {
    private final ObjectMapper objectMapper = new ObjectMapper();
    private RunApprovalRepository approvalRepository;
    private RunApprovalAuditFactRepository approvalAuditRepository;
    private ApprovalAlertService approvalAlertService;
    private RunRepository runRepository;
    private RunEventRepository eventRepository;
    private RunNodeExecutionRepository nodeRepository;
    private RunExecutionLeaseService leaseService;
    private RunDispatchOutboxService outboxService;
    private RunEventStream eventStream;
    private RunApprovalService service;

    @BeforeEach
    void setUp() {
        approvalRepository = mock(RunApprovalRepository.class);
        approvalAuditRepository = mock(RunApprovalAuditFactRepository.class);
        approvalAlertService = mock(ApprovalAlertService.class);
        runRepository = mock(RunRepository.class);
        eventRepository = mock(RunEventRepository.class);
        nodeRepository = mock(RunNodeExecutionRepository.class);
        leaseService = mock(RunExecutionLeaseService.class);
        outboxService = mock(RunDispatchOutboxService.class);
        eventStream = mock(RunEventStream.class);
        service = new RunApprovalService(
                approvalRepository, approvalAuditRepository, approvalAlertService,
                runRepository, eventRepository, nodeRepository,
                leaseService, outboxService, eventStream, objectMapper
        );
        when(approvalRepository.saveAndFlush(any(RunApprovalEntity.class))).thenAnswer(invocation -> {
            RunApprovalEntity item = invocation.getArgument(0);
            item.prePersist();
            return item;
        });
        when(approvalRepository.save(any(RunApprovalEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(approvalAuditRepository.save(any(RunApprovalAuditFactEntity.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(eventRepository.save(any(RunEventEntity.class))).thenAnswer(invocation -> {
            RunEventEntity item = invocation.getArgument(0);
            item.prePersist();
            return item;
        });
        when(nodeRepository.save(any(RunNodeExecutionEntity.class))).thenAnswer(invocation -> {
            RunNodeExecutionEntity item = invocation.getArgument(0);
            item.prePersist();
            return item;
        });
        when(runRepository.save(any(RunEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void suspensionPersistsCheckpointAndMovesRunToWaiting() throws Exception {
        RunEntity run = run("RUNNING");
        when(approvalRepository.findByTenantIdAndRunIdAndNodeId("tenant-a", "run-1", "approval"))
                .thenReturn(Optional.empty());
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(nodeRepository.findByTenantIdAndRunIdAndNodeId("tenant-a", "run-1", "approval"))
                .thenReturn(Optional.empty());
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(4L);

        RunApprovalService.ApprovalResult result = service.suspend("run-1", suspendRequest());

        assertFalse(result.duplicate());
        assertEquals("PENDING", result.item().status());
        assertEquals("WAITING_APPROVAL", run.getStatus());
        assertEquals("workflow.waiting_approval", run.getStage());
        verify(leaseService).requireActiveOwner("run-1", "tenant-a", "lease-1");
        verify(leaseService).release("run-1", new RunDto.LeaseMutationRequest("tenant-a", "lease-1"));
        verify(eventStream).publish(any(RunDto.EventView.class));
        verify(approvalAuditRepository).save(any(RunApprovalAuditFactEntity.class));
    }

    @Test
    void assignedRoleCanApproveAndDecisionIsIdempotent() {
        RunEntity run = run("WAITING_APPROVAL");
        RunApprovalEntity approval = approval();
        when(approvalRepository.lockByApprovalIdAndTenantId("approval-1", "tenant-a"))
                .thenReturn(Optional.of(approval));
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(5L);

        RunApprovalService.ApprovalResult decided = service.decide(
                "tenant-a", "admin@example.test", List.of("TENANT_ADMIN"), "approval-1",
                new RunDto.ApprovalDecisionRequest("approve", "looks good", 0)
        );
        RunApprovalService.ApprovalResult duplicate = service.decide(
                "tenant-a", "admin@example.test", List.of("TENANT_ADMIN"), "approval-1",
                new RunDto.ApprovalDecisionRequest("approve", "looks good", 0)
        );

        assertFalse(decided.duplicate());
        assertTrue(duplicate.duplicate());
        assertEquals("approve", decided.item().decision());
        assertEquals("PENDING", run.getStatus());
        assertEquals("QUEUED", run.getStage());
        verify(outboxService).requeueApprovalResume("tenant-a", "run-1");
    }

    @Test
    void decisionFailsClosedForUnassignedRole() {
        RunApprovalEntity approval = approval();
        when(approvalRepository.lockByApprovalIdAndTenantId("approval-1", "tenant-a"))
                .thenReturn(Optional.of(approval));

        ResponseStatusException error = assertThrows(ResponseStatusException.class, () -> service.decide(
                "tenant-a", "viewer@example.test", List.of("VIEWER"), "approval-1",
                new RunDto.ApprovalDecisionRequest("reject", "not allowed", 0)
        ));

        assertEquals(HttpStatus.FORBIDDEN, error.getStatusCode());
    }

    @Test
    void expiredApprovalUsesConfiguredTimeoutDecision() {
        RunEntity run = run("WAITING_APPROVAL");
        RunApprovalEntity approval = approval();
        approval.setExpiresAt(Instant.now().minusSeconds(1));
        when(approvalRepository.lockByApprovalIdAndTenantId("approval-1", "tenant-a"))
                .thenReturn(Optional.of(approval));
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(eventRepository.maxSequence("run-1", "tenant-a")).thenReturn(5L);

        RunApprovalService.ApprovalResult result = service.decide(
                "tenant-a", "admin@example.test", List.of("TENANT_ADMIN"), "approval-1",
                new RunDto.ApprovalDecisionRequest("approve", "late click", 0)
        );

        assertTrue(result.duplicate());
        assertTrue(result.item().timed_out());
        assertEquals("reject", result.item().decision());
        assertEquals("system:approval-timeout", result.item().decided_by());
    }

    @Test
    void listAppliesRoleVisibilityBeforeTheHundredItemLimit() {
        RunApprovalEntity approval = approval();
        when(approvalRepository.findVisible("tenant-a", "PENDING", false, List.of("TENANT_ADMIN")))
                .thenReturn(List.of(approval));

        List<RunDto.ApprovalView> result = service.list(
                "tenant-a", "admin@example.test", List.of("tenant_admin"), "pending"
        );

        assertEquals(1, result.size());
        assertEquals("approval-1", result.getFirst().approval_id());
        verify(approvalRepository).findVisible("tenant-a", "PENDING", false, List.of("TENANT_ADMIN"));
    }

    @Test
    void statsExposeAssignedQueueSlaRiskWithoutCrossRoleCounts() {
        RunApprovalRepository.ApprovalStatsProjection projection = mock(
                RunApprovalRepository.ApprovalStatsProjection.class
        );
        when(projection.getPendingCount()).thenReturn(7L);
        when(projection.getDueSoonCount()).thenReturn(2L);
        when(projection.getOverdueCount()).thenReturn(0L);
        when(projection.getOldestPendingAgeSeconds()).thenReturn(1200L);
        when(projection.getDecidedCount24h()).thenReturn(10L);
        when(projection.getTimedOutCount24h()).thenReturn(1L);
        when(projection.getAverageDecisionSeconds24h()).thenReturn(180L);
        when(approvalRepository.summarizeVisible(
                anyString(), anyBoolean(), anyList(), any(Instant.class), any(Instant.class), any(Instant.class)
        )).thenReturn(projection);

        RunDto.ApprovalStatsView result = service.stats(
                "tenant-a", "admin@example.test", List.of("TENANT_ADMIN"), 900
        );

        assertEquals("WARN", result.health());
        assertEquals(7, result.pending_count());
        assertEquals(0.1, result.timeout_rate_24h(), 0.0001);
        assertEquals(180, result.average_decision_seconds_24h());
        verify(approvalRepository).summarizeVisible(
                anyString(), anyBoolean(), anyList(), any(Instant.class), any(Instant.class), any(Instant.class)
        );
    }

    @Test
    void statsRejectUnsafeDueSoonWindows() {
        ResponseStatusException error = assertThrows(ResponseStatusException.class, () -> service.stats(
                "tenant-a", "admin@example.test", List.of("PLATFORM_ADMIN"), 59
        ));

        assertEquals(HttpStatus.BAD_REQUEST, error.getStatusCode());
    }

    @Test
    void trendUsesRetainedRoleScopedAuditFacts() {
        RunApprovalAuditFactRepository.ApprovalTrendProjection projection = mock(
                RunApprovalAuditFactRepository.ApprovalTrendProjection.class
        );
        when(projection.getDate()).thenReturn(LocalDate.now());
        when(projection.getCreatedCount()).thenReturn(3L);
        when(projection.getDecidedCount()).thenReturn(2L);
        when(projection.getApprovedCount()).thenReturn(1L);
        when(projection.getRejectedCount()).thenReturn(1L);
        when(projection.getTimedOutCount()).thenReturn(1L);
        when(projection.getCancelledCount()).thenReturn(0L);
        when(projection.getAverageDecisionSeconds()).thenReturn(75L);
        when(approvalAuditRepository.summarizeDaily(
                anyString(), anyBoolean(), anyList(), any(LocalDate.class), any(LocalDate.class)
        )).thenReturn(List.of(projection));

        RunDto.ApprovalTrendView result = service.trend(
                "tenant-a", "admin@example.test", List.of("tenant_admin"), 30
        );

        assertEquals(30, result.days());
        assertEquals("UTC", result.timezone());
        assertEquals(3, result.items().getFirst().created_count());
        assertEquals(75, result.items().getFirst().average_decision_seconds());
        verify(approvalAuditRepository).summarizeDaily(
                anyString(), anyBoolean(), anyList(), any(LocalDate.class), any(LocalDate.class)
        );
    }

    @Test
    void trendRejectsRetentionWindowsOverNinetyDays() {
        ResponseStatusException error = assertThrows(ResponseStatusException.class, () -> service.trend(
                "tenant-a", "admin@example.test", List.of("PLATFORM_ADMIN"), 91
        ));

        assertEquals(HttpStatus.BAD_REQUEST, error.getStatusCode());
    }

    private RunDto.InternalApprovalSuspendRequest suspendRequest() throws Exception {
        return new RunDto.InternalApprovalSuspendRequest(
                "tenant-a", "lease-1", "approval", "human.approval", "Publish answer", "Review it",
                List.of("TENANT_ADMIN"), 3600, "reject",
                objectMapper.readTree("{\"version\":1,\"node_id\":\"approval\",\"ordinal\":2,\"state\":{\"answer\":\"draft\"}}"),
                55
        );
    }

    private RunEntity run(String status) {
        RunEntity run = new RunEntity();
        run.setRunId("run-1");
        run.setTenantId("tenant-a");
        run.setConversationId("conv-1");
        run.setWorkflowId("wf-1");
        run.setWorkflowVersionId("version-1");
        run.setWorkflowVersionNo(1);
        run.setWorkflowChecksum("a".repeat(64));
        run.setCreatedBy("author@example.test");
        run.setStatus(status);
        run.setProgress(50);
        run.prePersist();
        return run;
    }

    private RunApprovalEntity approval() {
        RunApprovalEntity item = new RunApprovalEntity();
        item.setApprovalId("approval-1");
        item.setTenantId("tenant-a");
        item.setRunId("run-1");
        item.setNodeId("approval");
        item.setNodeType("human.approval");
        item.setTitle("Publish answer");
        item.setAssigneeRolesJson("[\"TENANT_ADMIN\"]");
        item.setCheckpointJson("{\"version\":1,\"node_id\":\"approval\",\"state\":{}}");
        item.setTimeoutAction("REJECT");
        item.setExpiresAt(Instant.now().plusSeconds(3600));
        item.prePersist();
        return item;
    }
}
