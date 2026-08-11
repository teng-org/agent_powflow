package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.product.AgentChatClient;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.RunDto;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.UUID;
import java.nio.charset.StandardCharsets;

@RestController
@RequestMapping("/api/v1/deployments")
public class PublishedDeploymentController {
    private static final long MAX_INPUT_BYTES = 1024 * 1024;
    private final ProductAssetService productAssetService;
    private final RunService runService;
    private final AgentChatClient agentChatClient;

    public PublishedDeploymentController(ProductAssetService productAssetService, RunService runService, AgentChatClient agentChatClient) {
        this.productAssetService = productAssetService;
        this.runService = runService;
        this.agentChatClient = agentChatClient;
    }

    @PostMapping("/{slug}/invoke")
    public Object invoke(
            @PathVariable String slug,
            @Valid @RequestBody ProductAssetDto.InvokeRequest request,
            HttpServletRequest httpRequest
    ) {
        if (Boolean.TRUE.equals(request.stream())) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "streaming is not supported by this endpoint");
        }
        long inputBytes = request.input().toString().getBytes(StandardCharsets.UTF_8).length;
        if (inputBytes > MAX_INPUT_BYTES) {
            throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE, "input exceeds 1 MiB");
        }
        String secret = bearer(httpRequest.getHeader("Authorization"));
        ProductAssetService.ResolvedInvocation resolved = productAssetService.resolveInvocation(slug, secret);
        String traceId = headerOr(httpRequest, "X-Trace-Id", UUID.randomUUID().toString());
        long startedNanos = System.nanoTime();
        var audit = productAssetService.beginInvocation(
                resolved, traceId, inputBytes
        );
        if ("RATE_LIMITED".equals(audit.getStatus())) {
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "deployment rate limit exceeded");
        }
        if ("AGENT".equals(resolved.deployment().getAssetType())) {
            try {
                AgentChatClient.AgentResult result = agentChatClient.invoke(
                        resolved.deployment().getTenantId(),
                        resolved.deployment().getOwnerUserId(),
                        resolved.agentSnapshot(),
                        request.input(),
                        request.conversation_id()
                );
                productAssetService.finishInvocation(
                        audit.getInvocationId(), result.runId(), startedNanos, "COMPLETED",
                        result.output().getBytes(StandardCharsets.UTF_8).length, null
                );
                return new ProductAssetDto.AgentInvokeCompletedResponse(
                        "completed",
                        resolved.deployment().getDeploymentId(),
                        resolved.deployment().getAssetType(),
                        resolved.deployment().getAssetId(),
                        resolved.deployment().getAssetVersionNo(),
                        result.runId(),
                        result.conversationId(),
                        traceId,
                        result.output(),
                        result.citations(),
                        result.metrics()
                );
            } catch (RuntimeException failure) {
                productAssetService.finishInvocation(audit.getInvocationId(), null, startedNanos, "REJECTED", 0, failure);
                throw failure;
            }
        }
        RunDto.MessageRunResponse accepted;
        try {
            accepted = runService.createPublishedApiRun(
                    resolved.deployment().getTenantId(),
                    resolved.deployment().getOwnerUserId(),
                    request.input(),
                    resolved.workflowId(),
                    resolved.workflowVersionNo()
            );
            productAssetService.finishInvocation(
                    audit.getInvocationId(), accepted.run().run_id(), startedNanos, "ACCEPTED", 0, null
            );
        } catch (RuntimeException failure) {
            productAssetService.finishInvocation(audit.getInvocationId(), null, startedNanos, "REJECTED", 0, failure);
            throw failure;
        }
        return new ProductAssetDto.InvokeAcceptedResponse(
                "accepted",
                resolved.deployment().getDeploymentId(),
                resolved.deployment().getAssetType(),
                resolved.deployment().getAssetId(),
                resolved.deployment().getAssetVersionNo(),
                accepted.run().run_id(),
                traceId,
                "/api/v1/deployments/" + slug + "/invoke/" + accepted.run().run_id()
        );
    }

    @GetMapping("/{slug}/invoke/{runId}")
    public ProductAssetDto.InvokeRunStatusResponse runStatus(
            @PathVariable String slug,
            @PathVariable String runId,
            HttpServletRequest httpRequest
    ) {
        String secret = bearer(httpRequest.getHeader("Authorization"));
        ProductAssetService.ResolvedInvocation resolved = productAssetService.resolveInvocation(slug, secret);
        var invocation = productAssetService.requireRunInvocation(resolved, runId);
        RunDto.RunView run = runService.internalGetRun(resolved.deployment().getTenantId(), runId);
        productAssetService.syncRunInvocation(
                invocation, run.status(), run.output_payload(), run.error_code(), run.started_at(), run.finished_at()
        );
        return new ProductAssetDto.InvokeRunStatusResponse(
                run.status().toLowerCase(), resolved.deployment().getDeploymentId(), run.run_id(), run.stage(), run.progress(),
                run.output_payload(), run.error_code(), run.error_message(), invocation.getTraceId(), run.started_at(), run.finished_at()
        );
    }

    private String bearer(String authorization) {
        if (authorization == null || !authorization.regionMatches(true, 0, "Bearer ", 0, 7)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "missing API key");
        }
        String secret = authorization.substring(7).trim();
        if (secret.isBlank()) throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "missing API key");
        return secret;
    }

    private String headerOr(HttpServletRequest request, String name, String fallback) {
        String value = request.getHeader(name);
        if (value == null || value.isBlank()) return fallback;
        String normalized = value.trim();
        if (normalized.length() > 128) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, name + " exceeds 128 characters");
        }
        return normalized;
    }
}
