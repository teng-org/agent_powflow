package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RunExecutionLeaseServiceTest {
    private RunRepository runRepository;
    private RunExecutionLeaseRepository leaseRepository;
    private RunExecutionLeaseService service;

    @BeforeEach
    void setUp() {
        runRepository = mock(RunRepository.class);
        leaseRepository = mock(RunExecutionLeaseRepository.class);
        ExecutionLeaseProperties properties = new ExecutionLeaseProperties();
        properties.setDurationSeconds(30);
        properties.setHeartbeatSeconds(10);
        service = new RunExecutionLeaseService(runRepository, leaseRepository, properties);
        when(leaseRepository.saveAndFlush(any(RunExecutionLeaseEntity.class))).thenAnswer(invocation -> {
            RunExecutionLeaseEntity lease = invocation.getArgument(0);
            if (lease.getCreatedAt() == null) lease.prePersist();
            return lease;
        });
        when(leaseRepository.save(any(RunExecutionLeaseEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void firstWorkerAcquiresLease() {
        RunEntity run = pendingRun("PENDING");
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(run));
        when(leaseRepository.lockByRunId("run-1")).thenReturn(Optional.empty());

        RunDto.LeaseView result = service.claim("run-1", claim("worker-a", "token-a"));

        assertTrue(result.acquired());
        assertEquals(1, result.attempt_no());
        assertEquals("ACQUIRED", result.reason());
    }

    @Test
    void concurrentWorkerCannotStealLiveLease() {
        RunExecutionLeaseEntity existing = lease("token-a", Instant.now().plusSeconds(30), 1);
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(pendingRun("RUNNING")));
        when(leaseRepository.lockByRunId("run-1")).thenReturn(Optional.of(existing));

        RunDto.LeaseView result = service.claim("run-1", claim("worker-b", "token-b"));

        assertFalse(result.acquired());
        assertEquals("LEASE_HELD", result.reason());
        assertEquals("worker-a", result.worker_id());
    }

    @Test
    void expiredLeaseCanBeTakenOverWithIncrementedAttempt() {
        RunExecutionLeaseEntity expired = lease("token-a", Instant.now().minusSeconds(1), 2);
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a")).thenReturn(Optional.of(pendingRun("RUNNING")));
        when(leaseRepository.lockByRunId("run-1")).thenReturn(Optional.of(expired));

        RunDto.LeaseView result = service.claim("run-1", claim("worker-b", "token-b"));

        assertTrue(result.acquired());
        assertEquals(3, result.attempt_no());
        assertEquals("worker-b", result.worker_id());
    }

    @Test
    void terminalRunCannotAcquireLease() {
        when(runRepository.lockByRunIdAndTenantId("run-1", "tenant-a"))
                .thenReturn(Optional.of(pendingRun("CANCELLED")));

        RunDto.LeaseView result = service.claim("run-1", claim("worker-a", "token-a"));

        assertFalse(result.acquired());
        assertEquals("RUN_TERMINAL", result.reason());
    }

    private RunDto.LeaseClaimRequest claim(String worker, String token) {
        return new RunDto.LeaseClaimRequest("tenant-a", worker, token);
    }

    private RunExecutionLeaseEntity lease(String token, Instant expiresAt, int attempt) {
        RunExecutionLeaseEntity lease = new RunExecutionLeaseEntity();
        lease.setRunId("run-1");
        lease.setTenantId("tenant-a");
        lease.setLeaseToken(token);
        lease.setWorkerId("worker-a");
        lease.setStatus("ACTIVE");
        lease.setAttemptNo(attempt);
        lease.setAcquiredAt(Instant.now().minusSeconds(5));
        lease.setHeartbeatAt(Instant.now().minusSeconds(5));
        lease.setExpiresAt(expiresAt);
        lease.prePersist();
        return lease;
    }

    private RunEntity pendingRun(String status) {
        RunEntity run = new RunEntity();
        run.setRunId("run-1");
        run.setTenantId("tenant-a");
        run.setConversationId("conv-1");
        run.setWorkflowId("wf-1");
        run.setWorkflowVersionId("version-1");
        run.setWorkflowVersionNo(1);
        run.setWorkflowChecksum("a".repeat(64));
        run.setCreatedBy("user-a");
        run.setStatus(status);
        run.prePersist();
        return run;
    }
}
