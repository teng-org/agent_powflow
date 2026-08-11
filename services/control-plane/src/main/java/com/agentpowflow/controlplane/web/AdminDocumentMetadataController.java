package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.DocumentMetadataDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/admin/kbs/{kbId}/documents/{docId}")
public class AdminDocumentMetadataController {
    private final KnowledgeExecutionClient executionClient;

    public AdminDocumentMetadataController(KnowledgeExecutionClient executionClient) {
        this.executionClient = executionClient;
    }

    @PatchMapping("/freeze")
    public Map<String, Object> freezeDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @Valid @RequestBody DocumentMetadataDto.FreezeRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return executionClient.setDocumentFrozen(claims.tenantId(), kbId, docId, request.frozen());
    }

    @PatchMapping("/chunks/{chunkId}/freeze")
    public Map<String, Object> freezeChunk(
            @PathVariable String kbId,
            @PathVariable String docId,
            @PathVariable String chunkId,
            @Valid @RequestBody DocumentMetadataDto.FreezeRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return executionClient.setChunkFrozen(claims.tenantId(), kbId, docId, chunkId, request.frozen());
    }

    @PutMapping("/chunks/{chunkId}/draft")
    public Map<String, Object> saveChunkDraft(
            @PathVariable String kbId,
            @PathVariable String docId,
            @PathVariable String chunkId,
            @Valid @RequestBody DocumentMetadataDto.ChunkDraftRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return executionClient.upsertChunkDraft(claims.tenantId(), kbId, docId, chunkId, request.action(), request.text());
    }

    @DeleteMapping("/chunks/{chunkId}/draft")
    public Map<String, Object> clearChunkDraft(
            @PathVariable String kbId,
            @PathVariable String docId,
            @PathVariable String chunkId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return executionClient.clearChunkDraft(claims.tenantId(), kbId, docId, chunkId);
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
