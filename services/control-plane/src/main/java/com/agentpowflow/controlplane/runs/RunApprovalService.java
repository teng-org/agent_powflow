package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.approvals.ApprovalAlertService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.HttpStatus;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

@Service
public class RunApprovalService {
    private static final int MAX_CHECKPOINT_CHARS = 1_000_000;
    private static final long MIN_DUE_SOON_SECONDS = 60;
    private static final long MAX_DUE_SOON_SECONDS = 86_400;
    private static final int MIN_TREND_DAYS = 1;
    private static final int MAX_TREND_DAYS = 90;
    private final RunApprovalRepository approvalRepository;
    private final RunApprovalAuditFactRepository approvalAuditRepository;
    private final ApprovalAlertService approvalAlertService;
    private final RunRepository runRepository;
    private final RunEventRepository eventRepository;
    private final RunNodeExecutionRepository nodeExecutionRepository;
    private final RunExecutionLeaseService leaseService;
    private final RunDispatchOutboxService outboxService;
    private final RunEventStream eventStream;
    private final ObjectMapper objectMapper;
    @Value("${control-plane.approvals.timeout-scan-enabled:true}")
    private boolean timeoutScanEnabled = true;

    public RunApprovalService(
            RunApprovalRepository approvalRepository,
            RunApprovalAuditFactRepository approvalAuditRepository,
            ApprovalAlertService approvalAlertService,
            RunRepository runRepository,
            RunEventRepository eventRepository,
            RunNodeExecutionRepository nodeExecutionRepository,
            RunExecutionLeaseService leaseService,
            RunDispatchOutboxService outboxService,
            RunEventStream eventStream,
            ObjectMapper objectMapper
    ) {
        this.approvalRepository = approvalRepository;
        this.approvalAuditRepository = approvalAuditRepository;
        this.approvalAlertService = approvalAlertService;
        this.runRepository = runRepository;
        this.eventRepository = eventRepository;
        this.nodeExecutionRepository = nodeExecutionRepository;
        this.leaseService = leaseService;
        this.outboxService = outboxService;
        this.eventStream = eventStream;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public ApprovalResult suspend(String runId, RunDto.InternalApprovalSuspendRequest request) {
        RunApprovalEntity existing = approvalRepository
                .findByTenantIdAndRunIdAndNodeId(request.tenant_id(), runId, request.node_id()).orElse(null);
        if (existing != null) {
            if (!"PENDING".equals(existing.getStatus())) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "approval node was already decided");
            }
            return new ApprovalResult(view(existing), true);
        }
        RunEntity run = runRepository.lockByRunIdAndTenantId(runId, request.tenant_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        if (!"RUNNING".equals(run.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "only a running run can request approval");
        }
        leaseService.requireActiveOwner(runId, request.tenant_id(), request.lease_token());
        String checkpointJson = writeJson(request.checkpoint());
        if (checkpointJson.length() > MAX_CHECKPOINT_CHARS) {
            throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE, "approval checkpoint exceeds 1 MB");
        }
        if (!request.node_id().equals(request.checkpoint().path("node_id").asText())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "approval checkpoint node does not match request node");
        }

        RunApprovalEntity approval = new RunApprovalEntity();
        approval.setTenantId(request.tenant_id());
        approval.setRunId(runId);
        approval.setNodeId(request.node_id());
        approval.setNodeType(request.node_type());
        approval.setTitle(request.title().trim());
        approval.setInstructions(request.instructions() == null ? "" : request.instructions());
        approval.setAssigneeRolesJson(writeJson(objectMapper.valueToTree(normalizeRoles(request.assignee_roles()))));
        approval.setCheckpointJson(checkpointJson);
        approval.setTimeoutAction(request.timeout_action().toUpperCase(Locale.ROOT));
        approval.setExpiresAt(Instant.now().plus(request.timeout_seconds(), ChronoUnit.SECONDS));
        approval = approvalRepository.saveAndFlush(approval);
        syncAuditFact(approval, null);

        run.setStatus("WAITING_APPROVAL");
        run.setStage("workflow.waiting_approval");
        run.setProgress(Math.max(run.getProgress(), request.progress()));
        run.setErrorCode("");
        run.setErrorMessage("");
        runRepository.save(run);
        syncWaitingNode(run, approval, request.checkpoint());

        RunDto.EventView event = saveEvent(
                run,
                "approval:" + approval.getApprovalId() + ":requested",
                "APPROVAL_REQUESTED",
                "WAITING_APPROVAL",
                "workflow.waiting_approval",
                run.getProgress(),
                objectMapper.createObjectNode()
                        .put("approval_id", approval.getApprovalId())
                        .put("node_id", approval.getNodeId())
                        .put("title", approval.getTitle())
                        .put("expires_at", approval.getExpiresAt().toString())
                        .set("assignee_roles", readJson(approval.getAssigneeRolesJson())),
                "control-plane"
        );
        leaseService.release(runId, new RunDto.LeaseMutationRequest(request.tenant_id(), request.lease_token()));
        eventStream.publish(event);
        return new ApprovalResult(view(approval), false);
    }

    @Transactional(readOnly = true)
    public List<RunDto.ApprovalView> list(String tenantId, String actorId, List<String> actorRoles, String status) {
        String normalized = status == null ? "" : status.trim().toUpperCase(Locale.ROOT);
        if (!normalized.isEmpty() && !Set.of("PENDING", "DECIDED", "CANCELLED").contains(normalized)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid approval status");
        }
        List<String> roles = visibilityRoles(actorRoles);
        boolean platformAdmin = roles.contains("PLATFORM_ADMIN");
        return approvalRepository.findVisible(tenantId, normalized, platformAdmin, roles).stream()
                .map(this::view).toList();
    }

    @Transactional(readOnly = true)
    public RunDto.ApprovalStatsView stats(
            String tenantId, String actorId, List<String> actorRoles, long dueSoonSeconds
    ) {
        if (dueSoonSeconds < MIN_DUE_SOON_SECONDS || dueSoonSeconds > MAX_DUE_SOON_SECONDS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "due_within_seconds must be between 60 and 86400");
        }
        List<String> roles = visibilityRoles(actorRoles);
        boolean platformAdmin = roles.contains("PLATFORM_ADMIN");
        Instant now = Instant.now();
        RunApprovalRepository.ApprovalStatsProjection value = approvalRepository.summarizeVisible(
                tenantId, platformAdmin, roles, now, now.plusSeconds(dueSoonSeconds), now.minus(24, ChronoUnit.HOURS)
        );
        String health = value.getOverdueCount() > 0
                ? "CRITICAL"
                : value.getDueSoonCount() > 0 || value.getTimedOutCount24h() > 0 ? "WARN" : "HEALTHY";
        double timeoutRate = value.getDecidedCount24h() == 0
                ? 0.0
                : (double) value.getTimedOutCount24h() / value.getDecidedCount24h();
        return new RunDto.ApprovalStatsView(
                health, value.getPendingCount(), value.getDueSoonCount(), value.getOverdueCount(),
                value.getOldestPendingAgeSeconds(), value.getDecidedCount24h(), value.getTimedOutCount24h(),
                timeoutRate, value.getAverageDecisionSeconds24h(), dueSoonSeconds, now
        );
    }

    @Transactional(readOnly = true)
    public RunDto.ApprovalTrendView trend(
            String tenantId, String actorId, List<String> actorRoles, int days
    ) {
        if (days < MIN_TREND_DAYS || days > MAX_TREND_DAYS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "days must be between 1 and 90");
        }
        List<String> roles = visibilityRoles(actorRoles);
        boolean platformAdmin = roles.contains("PLATFORM_ADMIN");
        Instant generatedAt = Instant.now();
        LocalDate endDate = generatedAt.atZone(ZoneOffset.UTC).toLocalDate();
        LocalDate startDate = endDate.minusDays(days - 1L);
        List<RunDto.ApprovalTrendPointView> items = approvalAuditRepository
                .summarizeDaily(tenantId, platformAdmin, roles, startDate, endDate)
                .stream()
                .map(value -> new RunDto.ApprovalTrendPointView(
                        value.getDate(), value.getCreatedCount(), value.getDecidedCount(),
                        value.getApprovedCount(), value.getRejectedCount(), value.getTimedOutCount(),
                        value.getCancelledCount(), value.getAverageDecisionSeconds()
                ))
                .toList();
        return new RunDto.ApprovalTrendView(
                days, startDate, endDate, "UTC", generatedAt, items
        );
    }

    @Transactional(readOnly = true)
    public RunDto.ApprovalView get(String tenantId, String actorId, List<String> actorRoles, String approvalId) {
        RunApprovalEntity item = approvalRepository.findByApprovalIdAndTenantId(approvalId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval not found"));
        requireCanAct(item, actorRoles);
        return view(item);
    }

    @Transactional
    public ApprovalResult decide(
            String tenantId, String actorId, List<String> actorRoles, String approvalId,
            RunDto.ApprovalDecisionRequest request
    ) {
        RunApprovalEntity approval = approvalRepository.lockByApprovalIdAndTenantId(approvalId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval not found"));
        requireCanAct(approval, actorRoles);
        String decision = request.decision().toUpperCase(Locale.ROOT);
        if ("DECIDED".equals(approval.getStatus())) {
            if (decision.equals(approval.getDecision())) return new ApprovalResult(view(approval), true);
            throw new ResponseStatusException(HttpStatus.CONFLICT, "approval already has a different decision");
        }
        if (!"PENDING".equals(approval.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "approval is not pending");
        }
        if (approval.getRowVersion() != request.revision()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "approval revision is stale");
        }
        Instant now = Instant.now();
        if (!approval.getExpiresAt().isAfter(now)) {
            finalizeDecision(approval, approval.getTimeoutAction(), "system:approval-timeout", "approval timed out", true, now);
            return new ApprovalResult(view(approval), true);
        }
        finalizeDecision(approval, decision, actorId, request.reason(), false, now);
        return new ApprovalResult(view(approval), false);
    }

    @Transactional
    public void cancelPendingForRun(String tenantId, String runId) {
        approvalRepository.findTopByTenantIdAndRunIdAndStatusOrderByDecidedAtDesc(tenantId, runId, "PENDING")
                .ifPresent(approval -> {
                    Instant now = Instant.now();
                    approval.setStatus("CANCELLED");
                    approvalRepository.save(approval);
                    syncAuditFact(approval, now);
                    approvalAlertService.onApprovalTerminal(approval, false, now);
                });
    }

    @Scheduled(fixedDelayString = "${control-plane.approvals.timeout-scan-ms:5000}")
    @Transactional
    public void expirePendingApprovals() {
        if (!timeoutScanEnabled) return;
        Instant now = Instant.now();
        for (RunApprovalEntity candidate : approvalRepository
                .findTop100ByStatusAndExpiresAtLessThanEqualOrderByExpiresAtAsc("PENDING", now)) {
            RunApprovalEntity approval = approvalRepository
                    .lockByApprovalIdAndTenantId(candidate.getApprovalId(), candidate.getTenantId()).orElse(null);
            if (approval == null || !"PENDING".equals(approval.getStatus()) || approval.getExpiresAt().isAfter(now)) continue;
            finalizeDecision(approval, approval.getTimeoutAction(), "system:approval-timeout", "approval timed out", true, now);
        }
    }

    public JsonNode resumeContext(String tenantId, String runId) {
        return approvalRepository.findTopByTenantIdAndRunIdAndStatusOrderByDecidedAtDesc(tenantId, runId, "DECIDED")
                .<JsonNode>map(approval -> {
                    var value = objectMapper.createObjectNode()
                            .put("approval_id", approval.getApprovalId())
                            .put("node_id", approval.getNodeId())
                            .put("decision", approval.getDecision().toLowerCase(Locale.ROOT))
                            .put("decided_by", approval.getDecidedBy())
                            .put("decision_reason", approval.getDecisionReason())
                            .put("decided_at", approval.getDecidedAt().toString())
                            .put("timed_out", approval.isTimedOut());
                    value.set("checkpoint", readJson(approval.getCheckpointJson()));
                    return value;
                })
                .orElse(null);
    }

    private void finalizeDecision(
            RunApprovalEntity approval, String decision, String actorId, String reason, boolean timedOut, Instant now
    ) {
        RunEntity run = runRepository.lockByRunIdAndTenantId(approval.getRunId(), approval.getTenantId())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found"));
        if (!"WAITING_APPROVAL".equals(run.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "run is not waiting for approval");
        }
        approval.setStatus("DECIDED");
        approval.setDecision(decision);
        approval.setDecisionReason(reason == null ? "" : reason.trim());
        approval.setDecidedBy(actorId);
        approval.setDecidedAt(now);
        approval.setTimedOut(timedOut);
        approvalRepository.saveAndFlush(approval);
        syncAuditFact(approval, now);
        approvalAlertService.onApprovalTerminal(approval, timedOut, now);

        run.setStatus("PENDING");
        run.setStage("QUEUED");
        run.setErrorCode("");
        run.setErrorMessage("");
        runRepository.save(run);
        outboxService.requeueApprovalResume(run.getTenantId(), run.getRunId());

        RunDto.EventView event = saveEvent(
                run,
                "approval:" + approval.getApprovalId() + ":decided",
                timedOut ? "APPROVAL_TIMED_OUT" : "APPROVAL_DECIDED",
                "PENDING",
                "QUEUED",
                run.getProgress(),
                objectMapper.createObjectNode()
                        .put("approval_id", approval.getApprovalId())
                        .put("node_id", approval.getNodeId())
                        .put("decision", decision.toLowerCase(Locale.ROOT))
                        .put("decided_by", actorId)
                        .put("decision_reason", approval.getDecisionReason())
                        .put("timed_out", timedOut),
                "control-plane"
        );
        eventStream.publish(event);
    }

    private void syncAuditFact(RunApprovalEntity approval, Instant terminalAt) {
        RunApprovalAuditFactEntity fact = approvalAuditRepository.findById(approval.getApprovalId())
                .orElseGet(RunApprovalAuditFactEntity::new);
        fact.setApprovalId(approval.getApprovalId());
        fact.setTenantId(approval.getTenantId());
        fact.setRunId(approval.getRunId());
        fact.setNodeId(approval.getNodeId());
        fact.setAssigneeRolesJson(approval.getAssigneeRolesJson());
        fact.setStatus(approval.getStatus());
        fact.setDecision(approval.getDecision());
        fact.setTimedOut(approval.isTimedOut());
        fact.setCreatedAt(approval.getCreatedAt());
        fact.setTerminalAt(terminalAt);
        fact.setDecisionDurationSeconds(terminalAt == null
                ? null
                : Math.max(0, Duration.between(approval.getCreatedAt(), terminalAt).getSeconds()));
        approvalAuditRepository.save(fact);
    }

    private void syncWaitingNode(RunEntity run, RunApprovalEntity approval, JsonNode checkpoint) {
        RunNodeExecutionEntity node = nodeExecutionRepository
                .findByTenantIdAndRunIdAndNodeId(run.getTenantId(), run.getRunId(), approval.getNodeId())
                .orElseGet(RunNodeExecutionEntity::new);
        node.setTenantId(run.getTenantId());
        node.setRunId(run.getRunId());
        node.setNodeId(approval.getNodeId());
        node.setNodeType(approval.getNodeType());
        node.setOrdinal(checkpoint.path("ordinal").asInt(0));
        if (node.getStartedAt() == null) node.setStartedAt(Instant.now());
        node.setStatus("WAITING_APPROVAL");
        node.setDetailsJson(writeJson(objectMapper.createObjectNode()
                .put("approval_id", approval.getApprovalId())
                .put("title", approval.getTitle())
                .put("expires_at", approval.getExpiresAt().toString())
                .set("assignee_roles", readJson(approval.getAssigneeRolesJson()))));
        node.setUpdatedAt(Instant.now());
        nodeExecutionRepository.save(node);
    }

    private RunDto.EventView saveEvent(
            RunEntity run, String externalId, String type, String status, String stage,
            int progress, JsonNode payload, String source
    ) {
        RunEventEntity event = new RunEventEntity();
        event.setTenantId(run.getTenantId());
        event.setRunId(run.getRunId());
        event.setExternalEventId(externalId);
        event.setSequenceNo(eventRepository.maxSequence(run.getRunId(), run.getTenantId()) + 1);
        event.setEventType(type);
        event.setStatus(status);
        event.setStage(stage);
        event.setProgress(progress);
        event.setPayloadJson(writeJson(payload));
        event.setSourceService(source);
        event = eventRepository.save(event);
        return new RunDto.EventView(
                event.getEventId(), event.getExternalEventId(), event.getTenantId(), event.getRunId(), event.getSequenceNo(),
                event.getEventType(), event.getStatus(), event.getStage(), event.getProgress(), readJson(event.getPayloadJson()),
                event.getSourceService(), event.getCreatedAt()
        );
    }

    private boolean canAct(RunApprovalEntity item, List<String> actorRoles) {
        Set<String> roles = new HashSet<>(normalizeRoles(actorRoles));
        if (roles.contains("PLATFORM_ADMIN")) return true;
        return readRoles(item).stream().anyMatch(roles::contains);
    }

    private void requireCanAct(RunApprovalEntity item, List<String> actorRoles) {
        if (!canAct(item, actorRoles)) throw new ResponseStatusException(HttpStatus.FORBIDDEN, "approval is not assigned to this actor");
    }

    private List<String> normalizeRoles(List<String> roles) {
        return roles.stream().map(role -> role.trim().toUpperCase(Locale.ROOT)).distinct().toList();
    }

    private List<String> visibilityRoles(List<String> roles) {
        List<String> normalized = normalizeRoles(roles == null ? List.of() : roles).stream()
                .filter(role -> !role.isBlank()).toList();
        return normalized.isEmpty() ? List.of("__NO_ASSIGNED_ROLE__") : normalized;
    }

    private List<String> readRoles(RunApprovalEntity item) {
        JsonNode value = readJson(item.getAssigneeRolesJson());
        if (!value.isArray()) return List.of();
        return java.util.stream.StreamSupport.stream(value.spliterator(), false)
                .map(JsonNode::asText).map(role -> role.toUpperCase(Locale.ROOT)).toList();
    }

    public RunDto.ApprovalView view(RunApprovalEntity item) {
        return new RunDto.ApprovalView(
                item.getApprovalId(), item.getTenantId(), item.getRunId(), item.getNodeId(), item.getNodeType(),
                item.getTitle(), item.getInstructions(), readRoles(item), item.getStatus(),
                item.getDecision().toLowerCase(Locale.ROOT), item.getDecisionReason(), item.getDecidedBy(),
                item.getTimeoutAction().toLowerCase(Locale.ROOT), item.getExpiresAt(), item.getDecidedAt(), item.isTimedOut(),
                item.getCreatedAt(), item.getUpdatedAt(), item.getRowVersion()
        );
    }

    private String writeJson(JsonNode value) {
        try { return objectMapper.writeValueAsString(value == null ? JsonNodeFactory.instance.objectNode() : value); }
        catch (JsonProcessingException ex) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid approval JSON", ex); }
    }

    private JsonNode readJson(String value) {
        try { return objectMapper.readTree(value == null || value.isBlank() ? "{}" : value); }
        catch (JsonProcessingException ex) { throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "stored approval JSON is invalid", ex); }
    }

    public record ApprovalResult(RunDto.ApprovalView item, boolean duplicate) {}
}
