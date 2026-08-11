package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseService;
import com.agentpowflow.controlplane.runs.InternalApiProperties;
import com.agentpowflow.controlplane.web.dto.KnowledgeBaseDto;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/api/v1/internal/chat")
public class InternalChatKnowledgeController {
    private final KnowledgeBaseService knowledgeBaseService;
    private final InternalApiProperties properties;

    public InternalChatKnowledgeController(
            KnowledgeBaseService knowledgeBaseService,
            InternalApiProperties properties
    ) {
        this.knowledgeBaseService = knowledgeBaseService;
        this.properties = properties;
    }

    @GetMapping("/knowledge-bases")
    public KnowledgeBaseDto.ListResponse listKnowledgeBases(
            @RequestParam("tenant_id") String tenantId,
            @RequestParam("owner_user_id") String ownerUserId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token
    ) {
        requireInternalToken(token);
        return new KnowledgeBaseDto.ListResponse(
                "ok", knowledgeBaseService.listAccessible(tenantId, ownerUserId)
        );
    }

    private void requireInternalToken(String token) {
        byte[] expected = properties.getToken().getBytes(StandardCharsets.UTF_8);
        byte[] actual = (token == null ? "" : token).getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(expected, actual)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid internal token");
        }
    }
}
