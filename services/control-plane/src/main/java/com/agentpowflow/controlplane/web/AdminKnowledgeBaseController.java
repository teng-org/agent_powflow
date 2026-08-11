package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseService;
import com.agentpowflow.controlplane.web.dto.KnowledgeBaseDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/kbs")
public class AdminKnowledgeBaseController {
    private final KnowledgeBaseService knowledgeBaseService;

    public AdminKnowledgeBaseController(KnowledgeBaseService knowledgeBaseService) {
        this.knowledgeBaseService = knowledgeBaseService;
    }

    @GetMapping
    public KnowledgeBaseDto.ListResponse list(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ListResponse("ok", knowledgeBaseService.list(claims.tenantId()));
    }

    @PostMapping
    public KnowledgeBaseDto.ItemResponse create(
            @Valid @RequestBody KnowledgeBaseDto.CreateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.create(claims.tenantId(), claims.sub(), request.name())
        );
    }

    @PatchMapping("/{kbId}")
    public KnowledgeBaseDto.ItemResponse rename(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.RenameRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.rename(claims.tenantId(), kbId, request.name())
        );
    }

    @PatchMapping("/{kbId}/visibility")
    public KnowledgeBaseDto.ItemResponse visibility(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.VisibilityRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.setVisibility(claims.tenantId(), kbId, request.visibility())
        );
    }

    @PatchMapping("/{kbId}/freeze")
    public KnowledgeBaseDto.ItemResponse freeze(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.FreezeRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.setFrozen(claims.tenantId(), kbId, request.frozen())
        );
    }

    @GetMapping("/{kbId}/bindings")
    public KnowledgeBaseDto.BindingListResponse bindings(
            @PathVariable String kbId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.BindingListResponse(
                "ok", kbId, knowledgeBaseService.listBindings(claims.tenantId(), kbId)
        );
    }

    @PutMapping("/{kbId}/bindings")
    public KnowledgeBaseDto.BindingResponse bind(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.BindingRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.BindingResponse(
                "ok", knowledgeBaseService.bind(claims.tenantId(), claims.sub(), kbId, request.target_tenant_id())
        );
    }

    @DeleteMapping("/{kbId}/bindings/{targetTenantId}")
    public KnowledgeBaseDto.DeleteResponse unbind(
            @PathVariable String kbId,
            @PathVariable String targetTenantId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.unbind(claims.tenantId(), kbId, targetTenantId);
        return new KnowledgeBaseDto.DeleteResponse("ok", targetTenantId);
    }

    @DeleteMapping("/{kbId}")
    public KnowledgeBaseDto.DeleteResponse delete(
            @PathVariable String kbId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.delete(claims.tenantId(), kbId);
        return new KnowledgeBaseDto.DeleteResponse("ok", kbId);
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
