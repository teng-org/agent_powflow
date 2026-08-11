package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.DocumentMetadataDto;
import org.junit.jupiter.api.Test;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AdminDocumentMetadataControllerTest {
    @Test
    void routesMetadataWritesThroughTenantScopedExecutionClient() {
        KnowledgeExecutionClient client = mock(KnowledgeExecutionClient.class);
        AdminDocumentMetadataController controller = new AdminDocumentMetadataController(client);
        TokenClaims claims = new TokenClaims(
                "actor-a", "tenant-a", List.of("KB_MANAGER"), "access",
                1L, 2L, "key-a", "active", false
        );
        var authentication = new UsernamePasswordAuthenticationToken(claims, "", List.of());
        when(client.setDocumentFrozen("tenant-a", "kb-a", "doc-a", true))
                .thenReturn(Map.of("status", "ok"));
        when(client.setChunkFrozen("tenant-a", "kb-a", "doc-a", "chunk-a", true))
                .thenReturn(Map.of("status", "ok"));
        when(client.upsertChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-a", "edit", "revised text"))
                .thenReturn(Map.of("status", "ok"));
        when(client.upsertChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-b", "delete", null))
                .thenReturn(Map.of("status", "ok"));
        when(client.clearChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-a"))
                .thenReturn(Map.of("status", "ok"));

        assertEquals("ok", controller.freezeDocument(
                "kb-a", "doc-a", new DocumentMetadataDto.FreezeRequest(true), authentication
        ).get("status"));
        assertEquals("ok", controller.freezeChunk(
                "kb-a", "doc-a", "chunk-a", new DocumentMetadataDto.FreezeRequest(true), authentication
        ).get("status"));
        assertEquals("ok", controller.saveChunkDraft(
                "kb-a", "doc-a", "chunk-a", new DocumentMetadataDto.ChunkDraftRequest("edit", "revised text"), authentication
        ).get("status"));
        assertEquals("ok", controller.saveChunkDraft(
                "kb-a", "doc-a", "chunk-b", new DocumentMetadataDto.ChunkDraftRequest("delete", null), authentication
        ).get("status"));
        assertEquals("ok", controller.clearChunkDraft(
                "kb-a", "doc-a", "chunk-a", authentication
        ).get("status"));

        verify(client).setDocumentFrozen("tenant-a", "kb-a", "doc-a", true);
        verify(client).setChunkFrozen("tenant-a", "kb-a", "doc-a", "chunk-a", true);
        verify(client).upsertChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-a", "edit", "revised text");
        verify(client).upsertChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-b", "delete", null);
        verify(client).clearChunkDraft("tenant-a", "kb-a", "doc-a", "chunk-a");
    }
}
