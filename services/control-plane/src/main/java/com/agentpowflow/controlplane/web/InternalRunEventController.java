package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.runs.InternalApiProperties;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.runs.RunExecutionLeaseService;
import com.agentpowflow.controlplane.runs.RunApprovalService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/api/v1/internal/runs")
public class InternalRunEventController {
    private final RunService runService;
    private final InternalApiProperties properties;
    private final RunExecutionLeaseService leaseService;
    private final RunApprovalService approvalService;

    public InternalRunEventController(
            RunService runService,
            InternalApiProperties properties,
            RunExecutionLeaseService leaseService,
            RunApprovalService approvalService
    ) {
        this.runService = runService;
        this.properties = properties;
        this.leaseService = leaseService;
        this.approvalService = approvalService;
    }

    @PostMapping("/{runId}/events")
    public RunDto.EventResponse append(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody RunDto.InternalEventRequest request
    ) {
        requireInternalToken(token);
        RunService.AppendResult result = runService.appendInternalEvent(runId, request);
        return new RunDto.EventResponse("ok", result.event(), result.duplicate());
    }

    @GetMapping("/{runId}/execution-context")
    public RunDto.ExecutionContextResponse executionContext(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token
    ) {
        requireInternalToken(token);
        return new RunDto.ExecutionContextResponse("ok", runService.internalExecutionContext(runId));
    }

    @PostMapping("/{runId}/lease/claim")
    public RunDto.LeaseResponse claimLease(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody RunDto.LeaseClaimRequest request
    ) {
        requireInternalToken(token);
        return new RunDto.LeaseResponse("ok", leaseService.claim(runId, request));
    }

    @PostMapping("/{runId}/lease/heartbeat")
    public RunDto.LeaseResponse heartbeatLease(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody RunDto.LeaseMutationRequest request
    ) {
        requireInternalToken(token);
        return new RunDto.LeaseResponse("ok", leaseService.heartbeat(runId, request));
    }

    @PostMapping("/{runId}/lease/release")
    public RunDto.LeaseResponse releaseLease(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody RunDto.LeaseMutationRequest request
    ) {
        requireInternalToken(token);
        return new RunDto.LeaseResponse("ok", leaseService.release(runId, request));
    }

    @PostMapping("/{runId}/approvals/suspend")
    public RunDto.ApprovalResponse suspendForApproval(
            @PathVariable String runId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody RunDto.InternalApprovalSuspendRequest request
    ) {
        requireInternalToken(token);
        RunApprovalService.ApprovalResult result = approvalService.suspend(runId, request);
        return new RunDto.ApprovalResponse("ok", result.item(), result.duplicate());
    }

    private void requireInternalToken(String supplied) {
        String expected = properties.getToken();
        if (supplied == null || expected == null || expected.isBlank() || !MessageDigest.isEqual(
                supplied.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8)
        )) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid internal token");
        }
    }
}
