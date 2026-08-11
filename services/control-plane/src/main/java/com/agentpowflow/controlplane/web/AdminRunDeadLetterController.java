package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.runs.RunDeadLetterService;
import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/admin/run-dead-letters")
public class AdminRunDeadLetterController {
    private final RunDeadLetterService service;
    public AdminRunDeadLetterController(RunDeadLetterService service){this.service=service;}
    @GetMapping public RunDto.DeadLetterListResponse list(@RequestParam(required=false) String status,Authentication auth){TokenClaims c=claims(auth);return new RunDto.DeadLetterListResponse("ok",service.list(c.tenantId(),status));}
    @GetMapping("/{id}") public RunDto.DeadLetterResponse get(@PathVariable String id,Authentication auth){TokenClaims c=claims(auth);return new RunDto.DeadLetterResponse("ok",service.get(c.tenantId(),id),false);}
    @GetMapping("/{id}/audit") public RunDto.DeadLetterAuditListResponse audit(@PathVariable String id,Authentication auth){TokenClaims c=claims(auth);return new RunDto.DeadLetterAuditListResponse("ok",service.audit(c.tenantId(),id));}
    @PostMapping("/{id}/replay") public RunDto.DeadLetterResponse replay(@PathVariable String id,Authentication auth){TokenClaims c=claims(auth);return new RunDto.DeadLetterResponse("ok",service.replay(c.tenantId(),id,c.sub()),false);}
    @PostMapping("/{id}/dismiss") public RunDto.DeadLetterResponse dismiss(@PathVariable String id,Authentication auth){TokenClaims c=claims(auth);return new RunDto.DeadLetterResponse("ok",service.dismiss(c.tenantId(),id,c.sub()),false);}
    private TokenClaims claims(Authentication auth){if(auth==null||!(auth.getPrincipal() instanceof TokenClaims c))throw new IllegalStateException("authenticated token claims missing");return c;}
}
