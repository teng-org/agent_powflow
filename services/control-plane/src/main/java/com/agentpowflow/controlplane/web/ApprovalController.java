package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.runs.RunApprovalService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/approvals")
public class ApprovalController {
    private final RunApprovalService service;

    public ApprovalController(RunApprovalService service) { this.service = service; }

    @GetMapping
    public RunDto.ApprovalListResponse list(
            @RequestParam(required = false) String status, Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new RunDto.ApprovalListResponse(
                "ok", service.list(claims.tenantId(), claims.sub(), claims.roles(), status)
        );
    }

    @GetMapping("/stats")
    public RunDto.ApprovalStatsResponse stats(
            @RequestParam(name = "due_within_seconds", defaultValue = "900") long dueWithinSeconds,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new RunDto.ApprovalStatsResponse(
                "ok", service.stats(claims.tenantId(), claims.sub(), claims.roles(), dueWithinSeconds)
        );
    }

    @GetMapping("/trend")
    public RunDto.ApprovalTrendResponse trend(
            @RequestParam(defaultValue = "30") int days,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new RunDto.ApprovalTrendResponse(
                "ok", service.trend(claims.tenantId(), claims.sub(), claims.roles(), days)
        );
    }

    @GetMapping("/{approvalId}")
    public RunDto.ApprovalResponse get(@PathVariable String approvalId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.ApprovalResponse(
                "ok", service.get(claims.tenantId(), claims.sub(), claims.roles(), approvalId), false
        );
    }

    @PostMapping("/{approvalId}/decision")
    public RunDto.ApprovalResponse decide(
            @PathVariable String approvalId,
            @Valid @RequestBody RunDto.ApprovalDecisionRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        RunApprovalService.ApprovalResult result = service.decide(
                claims.tenantId(), claims.sub(), claims.roles(), approvalId, request
        );
        return new RunDto.ApprovalResponse("ok", result.item(), result.duplicate());
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
