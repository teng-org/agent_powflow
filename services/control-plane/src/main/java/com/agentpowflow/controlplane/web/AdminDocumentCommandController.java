package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.tasks.DocumentIngestCommandService;
import com.agentpowflow.controlplane.tasks.DocumentBatchCommandService;
import com.agentpowflow.controlplane.tasks.DocumentMutationCommandService;
import com.agentpowflow.controlplane.tasks.DocumentUploadCommandService;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/admin/kbs")
public class AdminDocumentCommandController {
    private final DocumentIngestCommandService commandService;
    private final DocumentUploadCommandService uploadCommandService;
    private final DocumentMutationCommandService mutationCommandService;
    private final DocumentBatchCommandService batchCommandService;

    public AdminDocumentCommandController(
            DocumentIngestCommandService commandService,
            DocumentUploadCommandService uploadCommandService,
            DocumentMutationCommandService mutationCommandService,
            DocumentBatchCommandService batchCommandService
    ) {
        this.commandService = commandService;
        this.uploadCommandService = uploadCommandService;
        this.mutationCommandService = mutationCommandService;
        this.batchCommandService = batchCommandService;
    }

    @PostMapping(value = "/{kbId}/documents/upload", consumes = "multipart/form-data")
    public TaskDto.UploadResponse uploadDocuments(
            @PathVariable String kbId,
            @RequestPart("file") List<MultipartFile> files,
            @RequestParam(value = "process_mode", defaultValue = "UPLOAD_CLEAN_CHUNK_EMBED") String processMode,
            @RequestParam(value = "chunk_method", defaultValue = "sentence") String chunkMethod,
            @RequestParam(value = "chunk_delimiter", required = false) String chunkDelimiter,
            @RequestParam(value = "chunk_size", defaultValue = "500") int chunkSize,
            @RequestParam(value = "chunk_overlap", defaultValue = "50") int chunkOverlap,
            @RequestParam(value = "auto_summary", defaultValue = "false") boolean autoSummary,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        String trace = traceId == null || traceId.isBlank() ? UUID.randomUUID().toString() : traceId.trim();
        return uploadCommandService.upload(
                claims.tenantId(), claims.sub(), trace, kbId, files, processMode, chunkMethod,
                chunkDelimiter, chunkSize, chunkOverlap, autoSummary
        );
    }

    @PostMapping("/{kbId}/documents/ingest")
    public TaskDto.IngestAcceptedResponse ingestText(
            @PathVariable String kbId,
            @Valid @RequestBody TaskDto.IngestTextRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        String trace = traceId == null || traceId.isBlank() ? UUID.randomUUID().toString() : traceId.trim();
        return commandService.ingestText(claims.tenantId(), claims.sub(), trace, kbId, request);
    }

    @PostMapping("/{kbId}/documents/batch")
    public TaskDto.BatchAcceptedResponse batchDocuments(
            @PathVariable String kbId,
            @Valid @RequestBody TaskDto.BatchDocumentsRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return batchCommandService.submit(
                claims.tenantId(), claims.sub(), trace(traceId), kbId, request
        );
    }

    @PostMapping("/{kbId}/summarize")
    public TaskDto.DocumentCommandAcceptedResponse summarizeKnowledgeBase(
            @PathVariable String kbId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return mutationCommandService.summarizeKnowledgeBase(
                claims.tenantId(), claims.sub(), trace(traceId), kbId
        );
    }

    @PostMapping("/{kbId}/documents/{docId}/summarize")
    public TaskDto.DocumentCommandAcceptedResponse summarizeDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "summarize", null, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/chunk")
    public TaskDto.DocumentCommandAcceptedResponse chunkDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "chunk", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/reindex")
    public TaskDto.DocumentCommandAcceptedResponse reindexDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "reindex", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/chunks/commit")
    public TaskDto.DocumentCommandAcceptedResponse commitChunkDrafts(
            @PathVariable String kbId,
            @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "commit", null, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/move")
    public TaskDto.DocumentCommandAcceptedResponse moveDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "move", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/tags")
    public TaskDto.DocumentCommandAcceptedResponse tagDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "tag", request, traceId, authentication);
    }

    @DeleteMapping("/{kbId}/documents/{docId}")
    public TaskDto.DocumentCommandAcceptedResponse deleteDocument(
            @PathVariable String kbId,
            @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        return documentCommand(kbId, docId, "delete", null, traceId, authentication);
    }

    private TaskDto.DocumentCommandAcceptedResponse documentCommand(
            String kbId,
            String docId,
            String command,
            TaskDto.DocumentCommandRequest request,
            String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return mutationCommandService.submitDocument(
                claims.tenantId(), claims.sub(), trace(traceId), kbId, docId, command, request
        );
    }

    private String trace(String traceId) {
        return traceId == null || traceId.isBlank() ? UUID.randomUUID().toString() : traceId.trim();
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
