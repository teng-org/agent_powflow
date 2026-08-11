package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.runs.RunEventStream;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/v1")
public class ConversationRunController {
    private final RunService runService;
    private final RunEventStream eventStream;

    public ConversationRunController(RunService runService, RunEventStream eventStream) {
        this.runService = runService;
        this.eventStream = eventStream;
    }

    @GetMapping("/conversations")
    public RunDto.ConversationListResponse conversations(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.ConversationListResponse("ok", runService.listConversations(claims.tenantId(), claims.sub()));
    }

    @PostMapping("/conversations")
    public RunDto.ConversationResponse createConversation(
            @RequestBody(required = false) RunDto.CreateConversationRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        String title = request == null ? null : request.title();
        return new RunDto.ConversationResponse("ok", runService.createConversation(claims.tenantId(), claims.sub(), title));
    }

    @GetMapping("/conversations/{conversationId}/messages")
    public RunDto.MessageListResponse messages(@PathVariable String conversationId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.MessageListResponse("ok", runService.listMessages(claims.tenantId(), claims.sub(), conversationId));
    }

    @PostMapping("/conversations/{conversationId}/messages")
    public RunDto.MessageRunResponse createMessageRun(
            @PathVariable String conversationId,
            @Valid @RequestBody RunDto.CreateMessageRunRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return runService.createMessageRun(
                claims.tenantId(), claims.sub(), conversationId, request.content(), request.workflow_id(), request.workflow_version_no()
        );
    }

    @DeleteMapping("/conversations/{conversationId}")
    public RunDto.DeleteResponse deleteConversation(@PathVariable String conversationId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        runService.deleteConversation(claims.tenantId(), claims.sub(), conversationId);
        return new RunDto.DeleteResponse("ok", conversationId);
    }

    @GetMapping("/runs/{runId}")
    public RunDto.RunResponse run(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.RunResponse("ok", runService.getRun(claims.tenantId(), claims.sub(), runId));
    }

    @PostMapping("/runs/{runId}/cancel")
    public RunDto.EventResponse cancel(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.EventResponse("ok", runService.cancel(claims.tenantId(), claims.sub(), runId), false);
    }

    @GetMapping("/runs/{runId}/events/history")
    public RunDto.EventListResponse eventHistory(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.EventListResponse("ok", runService.listEvents(claims.tenantId(), claims.sub(), runId));
    }

    @GetMapping(value = "/runs/{runId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter events(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return eventStream.subscribe(runId, runService.listEvents(claims.tenantId(), claims.sub(), runId));
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
