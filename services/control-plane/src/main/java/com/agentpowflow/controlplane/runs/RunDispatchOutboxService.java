package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Set;

@Service
public class RunDispatchOutboxService {
    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private final RunDispatchOutboxRepository repository;
    private final RunRepository runRepository;
    private final ExecutionDispatchProperties properties;

    public RunDispatchOutboxService(
            RunDispatchOutboxRepository repository,
            RunRepository runRepository,
            ExecutionDispatchProperties properties
    ) {
        this.repository = repository;
        this.runRepository = runRepository;
        this.properties = properties;
    }

    @Transactional
    public RunDispatchOutboxEntity enqueue(String tenantId, String runId) {
        RunDispatchOutboxEntity outbox = new RunDispatchOutboxEntity();
        outbox.setTenantId(tenantId);
        outbox.setRunId(runId);
        outbox.setPayloadJson("{\"run_id\":\"" + runId + "\"}");
        outbox.setMaxAttempts(Math.max(1, properties.getMaxAttempts()));
        return repository.save(outbox);
    }

    @Transactional
    public List<RunDispatchOutboxEntity> claimBatch(String dispatcherId) {
        Instant now = Instant.now();
        List<RunDispatchOutboxEntity> items = repository.lockDispatchable(
                now, Math.max(1, Math.min(properties.getBatchSize(), 100))
        );
        Instant lockedUntil = now.plus(Math.max(5, properties.getClaimSeconds()), ChronoUnit.SECONDS);
        for (RunDispatchOutboxEntity item : items) {
            item.setStatus("IN_FLIGHT");
            item.setAttemptCount(item.getAttemptCount() + 1);
            item.setLockedBy(dispatcherId);
            item.setLockedUntil(lockedUntil);
        }
        return repository.saveAllAndFlush(items);
    }

    @Transactional
    public void markPublished(String outboxId, String dispatcherId) {
        RunDispatchOutboxEntity item = ownedInFlight(outboxId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setStatus("PUBLISHED");
        item.setPublishedAt(now);
        item.setAvailableAt(now);
        clearLock(item);
        item.setLastError("");
        repository.save(item);
    }

    @Transactional
    public void markFailure(String outboxId, String dispatcherId, Throwable failure) {
        RunDispatchOutboxEntity snapshot = repository.findById(outboxId).orElse(null);
        if (snapshot == null) return;
        RunEntity run = null;
        if (snapshot.getAttemptCount() >= snapshot.getMaxAttempts()) {
            run = runRepository.lockByRunIdAndTenantId(snapshot.getRunId(), snapshot.getTenantId()).orElse(null);
        }
        RunDispatchOutboxEntity item = ownedInFlight(outboxId, dispatcherId);
        if (item == null) return;
        Instant now = Instant.now();
        item.setLastError(truncate(failure == null ? "dispatch failed" : failure.getMessage(), 2000));
        clearLock(item);
        if (item.getAttemptCount() >= item.getMaxAttempts()) {
            item.setStatus("DEAD_LETTER");
            item.setDeadLetteredAt(now);
            item.setAvailableAt(now);
            markRunDeadLetter(item, run);
        } else {
            item.setStatus("RETRY");
            item.setAvailableAt(now.plus(backoffSeconds(item.getAttemptCount()), ChronoUnit.SECONDS));
        }
        repository.save(item);
    }

    @Transactional
    public void cancelForRun(String runId) {
        repository.lockByRunId(runId).ifPresent(item -> {
            if (!"PUBLISHED".equals(item.getStatus()) && !"DEAD_LETTER".equals(item.getStatus())) {
                item.setStatus("CANCELLED");
                clearLock(item);
                repository.save(item);
            }
        });
    }

    @Transactional
    public void cancelClaim(String outboxId, String dispatcherId) {
        RunDispatchOutboxEntity item = ownedInFlight(outboxId, dispatcherId);
        if (item == null) return;
        item.setStatus("CANCELLED");
        clearLock(item);
        repository.save(item);
    }

    @Transactional(readOnly = true)
    public List<RunDto.DispatchOutboxView> list(String tenantId, String status) {
        List<RunDispatchOutboxEntity> items = status == null || status.isBlank()
                ? repository.findTop100ByTenantIdOrderByCreatedAtDesc(tenantId)
                : repository.findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(tenantId, normalizeStatus(status));
        return items.stream().map(this::view).toList();
    }

    @Transactional
    public RunDto.DispatchOutboxView replay(String tenantId, String outboxId) {
        RunDispatchOutboxEntity snapshot = repository.findByOutboxIdAndTenantId(outboxId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "dispatch outbox item not found"));
        if (!"DEAD_LETTER".equals(snapshot.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "only dead-letter dispatches can be replayed");
        }
        RunEntity run = runRepository.lockByRunIdAndTenantId(snapshot.getRunId(), tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        if (TERMINAL.contains(run.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "terminal run cannot be replayed");
        }
        RunDispatchOutboxEntity item = repository.lockByOutboxId(outboxId)
                .filter(candidate -> tenantId.equals(candidate.getTenantId()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "dispatch outbox item not found"));
        if (!"DEAD_LETTER".equals(item.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "dispatch outbox item is no longer dead-lettered");
        }
        Instant now = Instant.now();
        item.setStatus("PENDING");
        item.setAttemptCount(0);
        item.setAvailableAt(now);
        item.setDeadLetteredAt(null);
        item.setLastError("");
        clearLock(item);
        run.setStage("QUEUED");
        run.setErrorCode("");
        run.setErrorMessage("");
        runRepository.save(run);
        return view(repository.saveAndFlush(item));
    }

    @Transactional
    public RunDispatchOutboxEntity requeueExecutionDeadLetter(String tenantId, String runId) {
        RunEntity run = runRepository.lockByRunIdAndTenantId(runId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        if (TERMINAL.contains(run.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "terminal run cannot be replayed");
        }
        RunDispatchOutboxEntity item = repository.lockByRunId(runId)
                .filter(candidate -> tenantId.equals(candidate.getTenantId()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "dispatch outbox item not found"));
        Instant now = Instant.now();
        item.setStatus("PENDING");
        item.setAttemptCount(0);
        item.setAvailableAt(now);
        item.setDeadLetteredAt(null);
        item.setPublishedAt(null);
        item.setLastError("");
        clearLock(item);
        run.setStage("QUEUED");
        run.setErrorCode("");
        run.setErrorMessage("");
        runRepository.save(run);
        return repository.saveAndFlush(item);
    }

    @Transactional
    public RunDispatchOutboxEntity requeueApprovalResume(String tenantId, String runId) {
        RunDispatchOutboxEntity item = repository.lockByRunId(runId)
                .filter(candidate -> tenantId.equals(candidate.getTenantId()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "dispatch outbox item not found"));
        Instant now = Instant.now();
        item.setEventType("RUN_APPROVAL_RESUME_REQUESTED");
        item.setStatus("PENDING");
        item.setAttemptCount(0);
        item.setAvailableAt(now);
        item.setPublishedAt(null);
        item.setDeadLetteredAt(null);
        item.setLastError("");
        clearLock(item);
        return repository.saveAndFlush(item);
    }

    @Transactional
    public void deleteForRun(String runId) {
        repository.deleteByRunId(runId);
    }

    private RunDispatchOutboxEntity ownedInFlight(String outboxId, String dispatcherId) {
        RunDispatchOutboxEntity item = repository.lockByOutboxId(outboxId).orElse(null);
        if (item == null || !"IN_FLIGHT".equals(item.getStatus()) || !dispatcherId.equals(item.getLockedBy())) return null;
        return item;
    }

    private void markRunDeadLetter(RunDispatchOutboxEntity item, RunEntity run) {
        if (run == null || TERMINAL.contains(run.getStatus())) return;
        run.setStage("DISPATCH_DEAD_LETTER");
        run.setErrorCode("DISPATCH_RETRY_EXHAUSTED");
        run.setErrorMessage(item.getLastError());
        runRepository.save(run);
    }

    private long backoffSeconds(int attempt) {
        long base = Math.max(1, properties.getRetryBaseSeconds());
        int exponent = Math.max(0, Math.min(attempt - 1, 30));
        long calculated;
        try {
            calculated = Math.multiplyExact(base, 1L << exponent);
        } catch (ArithmeticException ex) {
            calculated = Long.MAX_VALUE;
        }
        return Math.min(calculated, Math.max(base, properties.getRetryMaxSeconds()));
    }

    private String normalizeStatus(String status) {
        String value = status.trim().toUpperCase();
        if (!Set.of("PENDING", "IN_FLIGHT", "RETRY", "PUBLISHED", "DEAD_LETTER", "CANCELLED").contains(value)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid dispatch outbox status");
        }
        return value;
    }

    private void clearLock(RunDispatchOutboxEntity item) {
        item.setLockedBy("");
        item.setLockedUntil(null);
    }

    private String truncate(String value, int max) {
        String safe = value == null ? "" : value;
        return safe.length() <= max ? safe : safe.substring(0, max);
    }

    public RunDto.DispatchOutboxView view(RunDispatchOutboxEntity item) {
        return new RunDto.DispatchOutboxView(
                item.getOutboxId(), item.getTenantId(), item.getRunId(), item.getEventType(), item.getStatus(),
                item.getAttemptCount(), item.getMaxAttempts(), item.getAvailableAt(), item.getLockedBy(),
                item.getLockedUntil(), item.getLastError(), item.getPublishedAt(), item.getDeadLetteredAt(),
                item.getCreatedAt(), item.getUpdatedAt(), item.getRowVersion()
        );
    }
}
