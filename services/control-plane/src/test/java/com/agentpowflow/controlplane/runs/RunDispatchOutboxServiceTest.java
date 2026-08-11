package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RunDispatchOutboxServiceTest {
    private RunDispatchOutboxRepository repository;
    private RunRepository runRepository;
    private ExecutionDispatchProperties properties;
    private RunDispatchOutboxService service;

    @BeforeEach
    void setUp() {
        repository = mock(RunDispatchOutboxRepository.class);
        runRepository = mock(RunRepository.class);
        properties = new ExecutionDispatchProperties();
        properties.setMaxAttempts(3);
        properties.setRetryBaseSeconds(2);
        properties.setRetryMaxSeconds(10);
        service = new RunDispatchOutboxService(repository, runRepository, properties);
        when(repository.save(any(RunDispatchOutboxEntity.class))).thenAnswer(invocation -> persist(invocation.getArgument(0)));
        when(repository.saveAndFlush(any(RunDispatchOutboxEntity.class))).thenAnswer(invocation -> persist(invocation.getArgument(0)));
        when(repository.saveAllAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void enqueueCreatesOneTransactionalDispatchFact() {
        RunDispatchOutboxEntity item = service.enqueue("tenant-a", "run-1");

        assertNotNull(item.getOutboxId());
        assertEquals("PENDING", item.getStatus());
        assertEquals(3, item.getMaxAttempts());
        assertTrue(item.getPayloadJson().contains("run-1"));
    }

    @Test
    void claimUsesBoundedOwnershipAndIncrementsAttempt() {
        RunDispatchOutboxEntity item = item("PENDING", 0, "");
        when(repository.lockDispatchable(any(), anyInt())).thenReturn(List.of(item));

        List<RunDispatchOutboxEntity> claimed = service.claimBatch("dispatcher-a");

        assertEquals(1, claimed.size());
        assertEquals("IN_FLIGHT", item.getStatus());
        assertEquals(1, item.getAttemptCount());
        assertEquals("dispatcher-a", item.getLockedBy());
        assertNotNull(item.getLockedUntil());
    }

    @Test
    void failureBacksOffThenDeadLettersAndMarksRun() {
        RunDispatchOutboxEntity retry = item("IN_FLIGHT", 1, "dispatcher-a");
        when(repository.findById(retry.getOutboxId())).thenReturn(Optional.of(retry));
        when(repository.lockByOutboxId(retry.getOutboxId())).thenReturn(Optional.of(retry));
        Instant before = Instant.now();

        service.markFailure(retry.getOutboxId(), "dispatcher-a", new RuntimeException("temporary"));

        assertEquals("RETRY", retry.getStatus());
        assertTrue(retry.getAvailableAt().isAfter(before.plusSeconds(1)));

        RunDispatchOutboxEntity dead = item("IN_FLIGHT", 3, "dispatcher-a");
        RunEntity run = pendingRun();
        when(repository.findById(dead.getOutboxId())).thenReturn(Optional.of(dead));
        when(repository.lockByOutboxId(dead.getOutboxId())).thenReturn(Optional.of(dead));
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));

        service.markFailure(dead.getOutboxId(), "dispatcher-a", new RuntimeException("broker unavailable"));

        assertEquals("DEAD_LETTER", dead.getStatus());
        assertNotNull(dead.getDeadLetteredAt());
        assertEquals("DISPATCH_DEAD_LETTER", run.getStage());
        assertEquals("DISPATCH_RETRY_EXHAUSTED", run.getErrorCode());
    }

    @Test
    void manualReplayResetsDeadLetterWithoutRewritingTerminalRuns() {
        RunDispatchOutboxEntity dead = item("DEAD_LETTER", 3, "");
        dead.setDeadLetteredAt(Instant.now());
        RunEntity run = pendingRun();
        when(repository.findByOutboxIdAndTenantId(dead.getOutboxId(), "tenant-a")).thenReturn(Optional.of(dead));
        when(repository.lockByOutboxId(dead.getOutboxId())).thenReturn(Optional.of(dead));
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));

        RunDto.DispatchOutboxView replayed = service.replay("tenant-a", dead.getOutboxId());

        assertEquals("PENDING", replayed.status());
        assertEquals(0, replayed.attempt_count());
        assertEquals("QUEUED", run.getStage());
        verify(runRepository).save(run);
    }

    private RunDispatchOutboxEntity item(String status, int attempts, String lockedBy) {
        RunDispatchOutboxEntity item = new RunDispatchOutboxEntity();
        item.setTenantId("tenant-a");
        item.setRunId("run-1");
        item.setStatus(status);
        item.setAttemptCount(attempts);
        item.setMaxAttempts(3);
        item.setLockedBy(lockedBy);
        item.prePersist();
        return item;
    }

    private RunDispatchOutboxEntity persist(RunDispatchOutboxEntity item) {
        if (item.getOutboxId() == null) item.prePersist();
        return item;
    }

    private RunEntity pendingRun() {
        RunEntity run = new RunEntity();
        run.setRunId("run-1");
        run.setTenantId("tenant-a");
        run.setConversationId("conv-1");
        run.setWorkflowId("wf-1");
        run.setWorkflowVersionId("version-1");
        run.setWorkflowVersionNo(1);
        run.setWorkflowChecksum("a".repeat(64));
        run.setCreatedBy("user-a");
        run.prePersist();
        return run;
    }
}
