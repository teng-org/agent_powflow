package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.product.AgentChatClient;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Instant;
import java.util.UUID;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicLong;

@RestController
@RequestMapping("/api/v1/deployments")
public class PublishedDeploymentController {
    private static final long MAX_INPUT_BYTES = 1024 * 1024;
    private final ProductAssetService productAssetService;
    private final RunService runService;
    private final AgentChatClient agentChatClient;
    private final ObjectMapper objectMapper;

    public PublishedDeploymentController(ProductAssetService productAssetService, RunService runService, AgentChatClient agentChatClient, ObjectMapper objectMapper) {
        this.productAssetService = productAssetService;
        this.runService = runService;
        this.agentChatClient = agentChatClient;
        this.objectMapper = objectMapper;
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
                        publishedActorId(resolved, firstNonBlank(
                                request.input().path("user").asText(""),
                                request.input().path("user_id").asText("")
                        )),
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
                        result.metrics(),
                        result.memory()
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

    @PostMapping(value = "/{slug}/v1/chat/completions", produces = {MediaType.APPLICATION_JSON_VALUE, MediaType.TEXT_EVENT_STREAM_VALUE})
    public Object openAiChatCompletions(
            @PathVariable String slug,
            @RequestBody JsonNode request,
            HttpServletRequest httpRequest
    ) {
        String secret = bearer(httpRequest.getHeader("Authorization"));
        ProductAssetService.ResolvedInvocation resolved = productAssetService.resolveInvocation(slug, secret);
        if (!"AGENT".equals(resolved.deployment().getAssetType())) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "chat completions require an Agent deployment");
        }
        String message = openAiUserMessage(request.path("messages"));
        if (message.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "messages must contain a user message");
        }
        long inputBytes = request.toString().getBytes(StandardCharsets.UTF_8).length;
        if (inputBytes > MAX_INPUT_BYTES) {
            throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE, "input exceeds 1 MiB");
        }
        String traceId = headerOr(httpRequest, "X-Trace-Id", UUID.randomUUID().toString());
        String conversationId = firstNonBlank(
                request.path("conversation_id").asText(""),
                request.path("metadata").path("conversation_id").asText(""),
                httpRequest.getHeader("X-Conversation-Id")
        );
        String model = request.path("model").asText(slug);
        String actorId = publishedActorId(resolved, firstNonBlank(
                request.path("user").asText(""),
                request.path("metadata").path("user_id").asText(""),
                httpRequest.getHeader("X-End-User-Id")
        ));
        var audit = productAssetService.beginInvocation(resolved, traceId, inputBytes);
        if ("RATE_LIMITED".equals(audit.getStatus())) {
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "deployment rate limit exceeded");
        }
        JsonNode input = objectMapper.createObjectNode().put("message", message);
        if (!request.path("stream").asBoolean(false)) {
            long startedNanos = System.nanoTime();
            try {
                AgentChatClient.AgentResult result = agentChatClient.invoke(
                        resolved.deployment().getTenantId(), actorId,
                        resolved.agentSnapshot(), input, conversationId
                );
                long outputBytes = result.output().getBytes(StandardCharsets.UTF_8).length;
                productAssetService.finishInvocation(
                        audit.getInvocationId(), result.runId(), startedNanos, "COMPLETED", outputBytes, null
                );
                return openAiCompletion(result, model, traceId);
            } catch (RuntimeException failure) {
                productAssetService.finishInvocation(audit.getInvocationId(), null, startedNanos, "REJECTED", 0, failure);
                throw failure;
            }
        }
        return openAiStream(resolved, audit.getInvocationId(), input, conversationId, model, actorId);
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

    private SseEmitter openAiStream(
            ProductAssetService.ResolvedInvocation resolved,
            String invocationId,
            JsonNode input,
            String conversationId,
            String model,
            String actorId
    ) {
        SseEmitter emitter = new SseEmitter(30L * 60L * 1000L);
        long startedNanos = System.nanoTime();
        AtomicLong outputBytes = new AtomicLong();
        Thread.startVirtualThread(() -> {
            try {
                AgentChatClient.StreamResult result = agentChatClient.stream(
                        resolved.deployment().getTenantId(), actorId,
                        resolved.agentSnapshot(), input, conversationId,
                        event -> {
                            try {
                                if ("run".equals(event.event())) {
                                    sendOpenAiChunk(
                                            emitter, event.data().path("run_id").asText(), model,
                                            objectMapper.createObjectNode().put("role", "assistant"), null,
                                            event.data().path("conversation_id").asText(""), null, null
                                    );
                                } else if ("token".equals(event.event())) {
                                    String token = event.data().path("delta").asText("");
                                    outputBytes.addAndGet(token.getBytes(StandardCharsets.UTF_8).length);
                                    sendOpenAiChunk(
                                            emitter, event.data().path("run_id").asText(), model,
                                            objectMapper.createObjectNode().put("content", token), null,
                                            null, null, null
                                    );
                                }
                            } catch (IOException io) {
                                throw new StreamClosedException(io);
                            }
                        }
                );
                sendOpenAiChunk(
                        emitter, result.runId(), model, objectMapper.createObjectNode(), "stop",
                        result.conversationId(), result.citations(), result.memory()
                );
                emitter.send(SseEmitter.event().data("[DONE]"));
                productAssetService.finishInvocation(
                        invocationId, result.runId(), startedNanos, "COMPLETED", outputBytes.get(), null
                );
                emitter.complete();
            } catch (RuntimeException | IOException failure) {
                RuntimeException auditedFailure = failure instanceof RuntimeException runtime
                        ? runtime : new RuntimeException(failure);
                productAssetService.finishInvocation(
                        invocationId, null, startedNanos, "REJECTED", outputBytes.get(), auditedFailure
                );
                try {
                    ObjectNode error = objectMapper.createObjectNode();
                    error.putObject("error").put("message", "stream generation failed").put("type", "api_error");
                    emitter.send(SseEmitter.event().data(error));
                } catch (IOException ignored) {
                    // Client disconnects are already reflected in the invocation audit.
                }
                emitter.completeWithError(auditedFailure);
            }
        });
        return emitter;
    }

    private ObjectNode openAiCompletion(AgentChatClient.AgentResult result, String model, String traceId) {
        ObjectNode response = objectMapper.createObjectNode();
        response.put("id", "chatcmpl-" + result.runId());
        response.put("object", "chat.completion");
        response.put("created", Instant.now().getEpochSecond());
        response.put("model", model);
        ObjectNode choice = response.putArray("choices").addObject();
        choice.put("index", 0);
        choice.putObject("message").put("role", "assistant").put("content", result.output());
        choice.put("finish_reason", "stop");
        response.putObject("usage").put("prompt_tokens", 0).put("completion_tokens", 0).put("total_tokens", 0);
        ObjectNode extension = response.putObject("powflow");
        extension.put("run_id", result.runId());
        extension.put("conversation_id", result.conversationId());
        extension.put("trace_id", traceId);
        extension.set("citations", result.citations());
        extension.set("memory", result.memory());
        extension.set("metrics", result.metrics());
        return response;
    }

    private void sendOpenAiChunk(
            SseEmitter emitter,
            String runId,
            String model,
            ObjectNode delta,
            String finishReason,
            String conversationId,
            JsonNode citations,
            JsonNode memory
    ) throws IOException {
        ObjectNode chunk = objectMapper.createObjectNode();
        chunk.put("id", "chatcmpl-" + runId);
        chunk.put("object", "chat.completion.chunk");
        chunk.put("created", Instant.now().getEpochSecond());
        chunk.put("model", model);
        ObjectNode choice = chunk.putArray("choices").addObject();
        choice.put("index", 0);
        choice.set("delta", delta);
        if (finishReason == null) choice.putNull("finish_reason"); else choice.put("finish_reason", finishReason);
        if (conversationId != null || citations != null || memory != null) {
            ObjectNode extension = chunk.putObject("powflow");
            if (conversationId != null) extension.put("conversation_id", conversationId);
            if (citations != null) extension.set("citations", citations);
            if (memory != null) extension.set("memory", memory);
        }
        emitter.send(SseEmitter.event().data(chunk));
    }

    private String openAiUserMessage(JsonNode messages) {
        if (!messages.isArray()) return "";
        for (int i = messages.size() - 1; i >= 0; i--) {
            JsonNode item = messages.path(i);
            if (!"user".equals(item.path("role").asText())) continue;
            JsonNode content = item.path("content");
            if (content.isTextual()) return content.asText().trim();
            if (content.isArray()) {
                StringBuilder text = new StringBuilder();
                for (JsonNode part : content) {
                    if ("text".equals(part.path("type").asText()) && part.path("text").isTextual()) {
                        if (!text.isEmpty()) text.append('\n');
                        text.append(part.path("text").asText());
                    }
                }
                return text.toString().trim();
            }
        }
        return "";
    }

    private String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) return value.trim();
        }
        return "";
    }

    private String publishedActorId(ProductAssetService.ResolvedInvocation resolved, String externalUserId) {
        boolean anonymous = externalUserId == null || externalUserId.isBlank();
        String source = anonymous
                ? String.join(":",
                        resolved.deployment().getDeploymentId() == null ? "" : resolved.deployment().getDeploymentId(),
                        resolved.apiKey().getApiKeyId() == null ? "" : resolved.apiKey().getApiKeyId(),
                        "anonymous"
                )
                : String.join(":",
                        resolved.deployment().getDeploymentId() == null ? "" : resolved.deployment().getDeploymentId(),
                        "user",
                        externalUserId.trim()
                );
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(source.getBytes(StandardCharsets.UTF_8));
            return (anonymous ? "published:anonymous:" : "published:")
                    + HexFormat.of().formatHex(digest, 0, 20);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
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

    private static final class StreamClosedException extends RuntimeException {
        private StreamClosedException(IOException cause) {
            super(cause);
        }
    }
}
