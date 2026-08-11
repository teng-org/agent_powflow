package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Set;

import static org.springframework.http.HttpStatus.CONFLICT;
import static org.springframework.http.HttpStatus.NOT_FOUND;

@Service
public class RunExecutionLeaseService {
    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private final RunRepository runRepository;
    private final RunExecutionLeaseRepository leaseRepository;
    private final ExecutionLeaseProperties properties;

    public RunExecutionLeaseService(
            RunRepository runRepository,
            RunExecutionLeaseRepository leaseRepository,
            ExecutionLeaseProperties properties
    ) {
        this.runRepository = runRepository;
        this.leaseRepository = leaseRepository;
        this.properties = properties;
    }

    @Transactional
    public RunDto.LeaseView claim(String runId, RunDto.LeaseClaimRequest request) {
        RunEntity run = requireRun(runId, request.tenant_id());
        Instant now = Instant.now();
        if (TERMINAL.contains(run.getStatus())) {
            return view(runId, request.tenant_id(), false, "RUN_TERMINAL", null);
        }
        if (!Set.of("PENDING", "RUNNING").contains(run.getStatus())) {
            return view(runId, request.tenant_id(), false, "RUN_NOT_DISPATCHABLE", null);
        }

        RunExecutionLeaseEntity lease = leaseRepository.lockByRunId(runId).orElse(null);
        if (lease != null && "ACTIVE".equals(lease.getStatus()) && lease.getExpiresAt().isAfter(now)
                && !lease.getLeaseToken().equals(request.lease_token())) {
            return view(runId, request.tenant_id(), false, "LEASE_HELD", lease);
        }

        if (lease == null) {
            lease = new RunExecutionLeaseEntity();
            lease.setRunId(runId);
            lease.setTenantId(request.tenant_id());
            lease.setAttemptNo(1);
        } else if (!request.lease_token().equals(lease.getLeaseToken())) {
            lease.setAttemptNo(lease.getAttemptNo() + 1);
        }
        lease.setLeaseToken(request.lease_token());
        lease.setWorkerId(request.worker_id());
        lease.setStatus("ACTIVE");
        lease.setAcquiredAt(now);
        lease.setHeartbeatAt(now);
        lease.setExpiresAt(now.plus(durationSeconds(), ChronoUnit.SECONDS));
        lease.setReleasedAt(null);
        return view(runId, request.tenant_id(), true, "ACQUIRED", leaseRepository.saveAndFlush(lease));
    }

    @Transactional
    public RunDto.LeaseView heartbeat(String runId, RunDto.LeaseMutationRequest request) {
        RunEntity run = requireRun(runId, request.tenant_id());
        if (TERMINAL.contains(run.getStatus())) {
            releaseForTerminal(runId);
            return view(runId, request.tenant_id(), false, "RUN_TERMINAL", null);
        }
        RunExecutionLeaseEntity lease = leaseRepository.lockByRunId(runId)
                .orElseThrow(() -> new ResponseStatusException(CONFLICT, "execution lease missing"));
        Instant now = Instant.now();
        if (!"ACTIVE".equals(lease.getStatus()) || !lease.getLeaseToken().equals(request.lease_token())
                || !lease.getExpiresAt().isAfter(now)) {
            return view(runId, request.tenant_id(), false, "LEASE_LOST", lease);
        }
        lease.setHeartbeatAt(now);
        lease.setExpiresAt(now.plus(durationSeconds(), ChronoUnit.SECONDS));
        return view(runId, request.tenant_id(), true, "RENEWED", leaseRepository.saveAndFlush(lease));
    }

    @Transactional
    public RunDto.LeaseView release(String runId, RunDto.LeaseMutationRequest request) {
        requireRun(runId, request.tenant_id());
        RunExecutionLeaseEntity lease = leaseRepository.lockByRunId(runId).orElse(null);
        if (lease == null) return view(runId, request.tenant_id(), false, "LEASE_MISSING", null);
        if (!lease.getLeaseToken().equals(request.lease_token())) {
            return view(runId, request.tenant_id(), false, "LEASE_NOT_OWNER", lease);
        }
        releaseEntity(lease, Instant.now());
        return view(runId, request.tenant_id(), false, "RELEASED", leaseRepository.saveAndFlush(lease));
    }

    @Transactional
    public void requireActiveOwner(String runId, String tenantId, String leaseToken) {
        RunExecutionLeaseEntity lease = leaseRepository.lockByRunId(runId)
                .orElseThrow(() -> new ResponseStatusException(CONFLICT, "execution lease missing"));
        Instant now = Instant.now();
        if (!tenantId.equals(lease.getTenantId()) || !"ACTIVE".equals(lease.getStatus())
                || !leaseToken.equals(lease.getLeaseToken()) || !lease.getExpiresAt().isAfter(now)) {
            throw new ResponseStatusException(CONFLICT, "execution lease is no longer owned by this worker");
        }
    }

    @Transactional
    public void releaseForTerminal(String runId) {
        RunExecutionLeaseEntity lease = leaseRepository.lockByRunId(runId).orElse(null);
        if (lease != null && "ACTIVE".equals(lease.getStatus())) {
            releaseEntity(lease, Instant.now());
            leaseRepository.save(lease);
        }
    }

    @Transactional
    public void deleteForRun(String runId) {
        leaseRepository.deleteById(runId);
    }

    private void releaseEntity(RunExecutionLeaseEntity lease, Instant now) {
        lease.setStatus("RELEASED");
        lease.setReleasedAt(now);
        lease.setExpiresAt(now);
    }

    private RunEntity requireRun(String runId, String tenantId) {
        return runRepository.lockByRunIdAndTenantId(runId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(NOT_FOUND, "run not found"));
    }

    private RunDto.LeaseView view(
            String runId, String tenantId, boolean acquired, String reason, RunExecutionLeaseEntity lease
    ) {
        return new RunDto.LeaseView(
                runId, tenantId, acquired, reason,
                lease == null ? "" : lease.getWorkerId(), lease == null ? 0 : lease.getAttemptNo(),
                lease == null ? null : lease.getExpiresAt(), durationSeconds(), heartbeatSeconds()
        );
    }

    private long durationSeconds() { return Math.max(5, properties.getDurationSeconds()); }
    private long heartbeatSeconds() {
        return Math.max(1, Math.min(properties.getHeartbeatSeconds(), durationSeconds() / 2));
    }
}
