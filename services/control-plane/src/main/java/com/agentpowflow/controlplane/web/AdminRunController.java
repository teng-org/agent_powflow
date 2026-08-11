package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/runs")
public class AdminRunController {
    private final RunService service;

    public AdminRunController(RunService service) {
        this.service = service;
    }

    @GetMapping("/{runId}")
    public RunDto.RunResponse get(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.RunResponse("ok", service.adminGetRun(claims.tenantId(), runId));
    }

    @GetMapping("/{runId}/events")
    public RunDto.EventListResponse events(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.EventListResponse("ok", service.adminListEvents(claims.tenantId(), runId));
    }

    @GetMapping("/{runId}/nodes")
    public RunDto.NodeExecutionListResponse nodes(@PathVariable String runId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.NodeExecutionListResponse("ok", service.adminListNodeExecutions(claims.tenantId(), runId));
    }

    @GetMapping
    public RunDto.RunListResponse list(
            @RequestParam(required = false) String status,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new RunDto.RunListResponse("ok", service.adminListRuns(claims.tenantId(), status));
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
