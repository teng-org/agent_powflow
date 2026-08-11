package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.runs.RunDispatchOutboxService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/run-dispatches")
public class AdminRunDispatchController {
    private final RunDispatchOutboxService service;

    public AdminRunDispatchController(RunDispatchOutboxService service) {
        this.service = service;
    }

    @GetMapping
    public RunDto.DispatchOutboxListResponse list(
            @RequestParam(required = false) String status,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new RunDto.DispatchOutboxListResponse("ok", service.list(claims.tenantId(), status));
    }

    @PostMapping("/{outboxId}/replay")
    public RunDto.DispatchOutboxResponse replay(@PathVariable String outboxId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new RunDto.DispatchOutboxResponse("ok", service.replay(claims.tenantId(), outboxId));
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
