package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.tasks.TaskDispatchOutboxService;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/task-dispatches")
public class AdminTaskDispatchController {
    private final TaskDispatchOutboxService service;

    public AdminTaskDispatchController(TaskDispatchOutboxService service) {
        this.service = service;
    }

    @GetMapping
    public TaskDto.DispatchOutboxListResponse list(
            @RequestParam(required = false) String status,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.DispatchOutboxListResponse("ok", service.list(claims.tenantId(), status));
    }

    @PostMapping("/{outboxId}/replay")
    public TaskDto.DispatchOutboxResponse replay(@PathVariable String outboxId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.DispatchOutboxResponse(
                "ok",
                claims.roles().contains("PLATFORM_ADMIN")
                        ? service.replayPlatform(outboxId)
                        : service.replay(claims.tenantId(), outboxId)
        );
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
