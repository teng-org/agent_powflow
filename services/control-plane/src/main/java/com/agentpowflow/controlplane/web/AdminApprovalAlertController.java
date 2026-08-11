package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.approvals.ApprovalAlertService;
import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.web.dto.ApprovalAlertDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/approval-alerts")
public class AdminApprovalAlertController {
    private final ApprovalAlertService service;

    public AdminApprovalAlertController(ApprovalAlertService service) { this.service = service; }

    @GetMapping
    public ApprovalAlertDto.AlertListResponse list(
            @RequestParam(required = false) String status, Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ApprovalAlertDto.AlertListResponse("ok", service.list(claims.tenantId(), status));
    }

    @GetMapping("/stats")
    public ApprovalAlertDto.StatsResponse stats(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new ApprovalAlertDto.StatsResponse("ok", service.stats(claims.tenantId()));
    }

    @GetMapping("/config")
    public ApprovalAlertDto.ConfigResponse config(Authentication authentication) {
        claims(authentication);
        return new ApprovalAlertDto.ConfigResponse("ok", service.config());
    }

    @PostMapping("/evaluate")
    public ApprovalAlertDto.EvaluationResponse evaluate(Authentication authentication) {
        claims(authentication);
        return new ApprovalAlertDto.EvaluationResponse("ok", service.evaluate());
    }

    @PostMapping("/{alertId}/acknowledge")
    public ApprovalAlertDto.AlertResponse acknowledge(
            @PathVariable String alertId, Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ApprovalAlertDto.AlertResponse(
                "ok", service.acknowledge(claims.tenantId(), claims.sub(), alertId), false
        );
    }

    @PostMapping("/{alertId}/replay")
    public ApprovalAlertDto.AlertResponse replay(
            @PathVariable String alertId, Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ApprovalAlertDto.AlertResponse("ok", service.replay(claims.tenantId(), alertId), false);
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
