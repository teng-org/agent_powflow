package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseService;
import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.tasks.DocumentIngestCommandService;
import com.agentpowflow.controlplane.tasks.DocumentUploadCommandService;
import com.agentpowflow.controlplane.tasks.DocumentMutationCommandService;
import com.agentpowflow.controlplane.tasks.DocumentBatchCommandService;
import com.agentpowflow.controlplane.web.dto.DocumentMetadataDto;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import com.agentpowflow.controlplane.web.dto.KnowledgeBaseDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/knowledge-bases")
public class WorkspaceKnowledgeBaseController {
    private final KnowledgeBaseService knowledgeBaseService;
    private final KnowledgeExecutionClient executionClient;
    private final DocumentIngestCommandService ingestCommandService;
    private final DocumentUploadCommandService uploadCommandService;
    private final DocumentMutationCommandService mutationCommandService;
    private final DocumentBatchCommandService batchCommandService;

    public WorkspaceKnowledgeBaseController(
            KnowledgeBaseService knowledgeBaseService,
            KnowledgeExecutionClient executionClient,
            DocumentIngestCommandService ingestCommandService,
            DocumentUploadCommandService uploadCommandService,
            DocumentMutationCommandService mutationCommandService,
            DocumentBatchCommandService batchCommandService
    ) {
        this.knowledgeBaseService = knowledgeBaseService;
        this.executionClient = executionClient;
        this.ingestCommandService = ingestCommandService;
        this.uploadCommandService = uploadCommandService;
        this.mutationCommandService = mutationCommandService;
        this.batchCommandService = batchCommandService;
    }

    @GetMapping
    public KnowledgeBaseDto.ListResponse list(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ListResponse(
                "ok", knowledgeBaseService.listOwned(claims.tenantId(), claims.sub())
        );
    }

    @PostMapping
    public KnowledgeBaseDto.ItemResponse create(
            @Valid @RequestBody KnowledgeBaseDto.CreateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.create(
                        claims.tenantId(), claims.sub(), request.name(), request.description(), request.tags(), request.visibility()
                )
        );
    }

    @GetMapping("/{kbId}/documents")
    public Map<String, Object> documents(
            @PathVariable String kbId,
            @RequestParam(value = "limit", defaultValue = "100") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return executionClient.listDocuments(claims.tenantId(), kbId, limit);
    }

    @GetMapping("/{kbId}/documents/{docId}/chunks")
    public Map<String, Object> chunks(
            @PathVariable String kbId, @PathVariable String docId,
            @RequestParam(value = "limit", defaultValue = "400") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return executionClient.listDocumentChunks(claims.tenantId(), kbId, docId, limit);
    }

    @GetMapping("/{kbId}/rag/indexes")
    public Map<String, Object> ragIndexes(@PathVariable String kbId, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.listRagIndexes(claims.tenantId(), kbId);
    }

    @PostMapping("/{kbId}/rag/indexes/bootstrap")
    public Map<String, Object> bootstrapRagIndex(@PathVariable String kbId, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.bootstrapRagIndex(claims.tenantId(), kbId);
    }

    @PostMapping("/{kbId}/rag/indexes/build")
    public Map<String, Object> buildRagIndex(
            @PathVariable String kbId,
            @RequestBody Map<String, Object> request,
            Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.buildRagIndex(claims.tenantId(), kbId, request == null ? Map.of() : request);
    }

    @PostMapping("/{kbId}/rag/indexes/{revision}/evaluate")
    public Map<String, Object> evaluateRagIndex(
            @PathVariable String kbId,
            @PathVariable String revision,
            @RequestBody Map<String, Object> request,
            Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.evaluateRagIndex(
                claims.tenantId(), kbId, safeRevision(revision), request == null ? Map.of() : request
        );
    }

    @PostMapping("/{kbId}/rag/indexes/{revision}/activate")
    public Map<String, Object> activateRagIndex(
            @PathVariable String kbId, @PathVariable String revision, Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.activateRagIndex(
                claims.tenantId(), kbId, safeRevision(revision), claims.sub(), false
        );
    }

    @PostMapping("/{kbId}/rag/indexes/{revision}/rollback")
    public Map<String, Object> rollbackRagIndex(
            @PathVariable String kbId, @PathVariable String revision, Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.activateRagIndex(
                claims.tenantId(), kbId, safeRevision(revision), claims.sub(), true
        );
    }

    @DeleteMapping("/{kbId}/rag/indexes/{revision}")
    public Map<String, Object> cleanupRagIndex(
            @PathVariable String kbId, @PathVariable String revision, Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.cleanupFailedRagIndex(claims.tenantId(), kbId, safeRevision(revision));
    }

    @PostMapping("/{kbId}/documents/ingest")
    public TaskDto.IngestAcceptedResponse ingestText(
            @PathVariable String kbId,
            @Valid @RequestBody TaskDto.IngestTextRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return ingestCommandService.ingestText(claims.tenantId(), claims.sub(), trace(traceId), kbId, request);
    }

    @PostMapping(value = "/{kbId}/documents/upload", consumes = "multipart/form-data")
    public TaskDto.UploadResponse upload(
            @PathVariable String kbId,
            @RequestPart("file") List<MultipartFile> files,
            @RequestParam(value = "process_mode", defaultValue = "UPLOAD_CLEAN_CHUNK_EMBED") String processMode,
            @RequestParam(value = "chunk_method", defaultValue = "sentence") String chunkMethod,
            @RequestParam(value = "chunk_size", defaultValue = "500") int chunkSize,
            @RequestParam(value = "chunk_overlap", defaultValue = "50") int chunkOverlap,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return uploadCommandService.upload(
                claims.tenantId(), claims.sub(), trace(traceId), kbId, files, processMode, chunkMethod,
                null, chunkSize, chunkOverlap, false
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
                "ok", knowledgeBaseService.renameOwned(claims.tenantId(), claims.sub(), kbId, request.name())
        );
    }

    @PatchMapping("/{kbId}/metadata")
    public KnowledgeBaseDto.ItemResponse update(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.UpdateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.updateOwned(
                        claims.tenantId(), claims.sub(), kbId, request.name(), request.description(),
                        request.tags(), request.visibility()
                )
        );
    }

    @DeleteMapping("/{kbId}")
    public KnowledgeBaseDto.DeleteResponse delete(@PathVariable String kbId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.deleteOwned(claims.tenantId(), claims.sub(), kbId);
        return new KnowledgeBaseDto.DeleteResponse("ok", kbId);
    }

    @PatchMapping("/{kbId}/visibility")
    public KnowledgeBaseDto.ItemResponse visibility(
            @PathVariable String kbId,
            @Valid @RequestBody KnowledgeBaseDto.VisibilityRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new KnowledgeBaseDto.ItemResponse(
                "ok", knowledgeBaseService.setOwnedVisibility(
                        claims.tenantId(), claims.sub(), kbId, request.visibility()
                )
        );
    }

    @PatchMapping("/{kbId}/freeze")
    public KnowledgeBaseDto.ItemResponse freeze(
            @PathVariable String kbId, @Valid @RequestBody KnowledgeBaseDto.FreezeRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return new KnowledgeBaseDto.ItemResponse("ok", knowledgeBaseService.setFrozen(claims.tenantId(), kbId, request.frozen()));
    }

    @PostMapping("/{kbId}/summarize")
    public TaskDto.DocumentCommandAcceptedResponse summarizeKb(
            @PathVariable String kbId, @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return mutationCommandService.summarizeKnowledgeBase(claims.tenantId(), claims.sub(), trace(traceId), kbId);
    }

    @PostMapping("/{kbId}/documents/batch")
    public TaskDto.BatchAcceptedResponse batch(
            @PathVariable String kbId, @Valid @RequestBody TaskDto.BatchDocumentsRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            Authentication authentication
    ) {
        TokenClaims claims = owned(authentication, kbId);
        return batchCommandService.submit(claims.tenantId(), claims.sub(), trace(traceId), kbId, request);
    }

    @PostMapping("/{kbId}/documents/{docId}/summarize")
    public TaskDto.DocumentCommandAcceptedResponse summarizeDoc(@PathVariable String kbId, @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "summarize", null, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/chunk")
    public TaskDto.DocumentCommandAcceptedResponse chunkDoc(@PathVariable String kbId, @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "chunk", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/reindex")
    public TaskDto.DocumentCommandAcceptedResponse reindexDoc(@PathVariable String kbId, @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "reindex", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/chunks/commit")
    public TaskDto.DocumentCommandAcceptedResponse commitDrafts(@PathVariable String kbId, @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "commit", null, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/move")
    public TaskDto.DocumentCommandAcceptedResponse moveDoc(@PathVariable String kbId, @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "move", request, traceId, authentication);
    }

    @PostMapping("/{kbId}/documents/{docId}/tags")
    public TaskDto.DocumentCommandAcceptedResponse tagDoc(@PathVariable String kbId, @PathVariable String docId,
            @Valid @RequestBody TaskDto.DocumentCommandRequest request,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "tag", request, traceId, authentication);
    }

    @DeleteMapping("/{kbId}/documents/{docId}")
    public TaskDto.DocumentCommandAcceptedResponse deleteDoc(@PathVariable String kbId, @PathVariable String docId,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId, Authentication authentication) {
        return documentCommand(kbId, docId, "delete", null, traceId, authentication);
    }

    @PatchMapping("/{kbId}/documents/{docId}/freeze")
    public Map<String, Object> freezeDoc(@PathVariable String kbId, @PathVariable String docId,
            @Valid @RequestBody DocumentMetadataDto.FreezeRequest request, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.setDocumentFrozen(claims.tenantId(), kbId, docId, request.frozen());
    }

    @PatchMapping("/{kbId}/documents/{docId}/chunks/{chunkId}/freeze")
    public Map<String, Object> freezeChunk(@PathVariable String kbId, @PathVariable String docId, @PathVariable String chunkId,
            @Valid @RequestBody DocumentMetadataDto.FreezeRequest request, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.setChunkFrozen(claims.tenantId(), kbId, docId, chunkId, request.frozen());
    }

    @PutMapping("/{kbId}/documents/{docId}/chunks/{chunkId}/draft")
    public Map<String, Object> saveDraft(@PathVariable String kbId, @PathVariable String docId, @PathVariable String chunkId,
            @Valid @RequestBody DocumentMetadataDto.ChunkDraftRequest request, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.upsertChunkDraft(claims.tenantId(), kbId, docId, chunkId, request.action(), request.text());
    }

    @DeleteMapping("/{kbId}/documents/{docId}/chunks/{chunkId}/draft")
    public Map<String, Object> clearDraft(@PathVariable String kbId, @PathVariable String docId, @PathVariable String chunkId,
            Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return executionClient.clearChunkDraft(claims.tenantId(), kbId, docId, chunkId);
    }

    private TaskDto.DocumentCommandAcceptedResponse documentCommand(String kbId, String docId, String command,
            TaskDto.DocumentCommandRequest request, String traceId, Authentication authentication) {
        TokenClaims claims = owned(authentication, kbId);
        return mutationCommandService.submitDocument(claims.tenantId(), claims.sub(), trace(traceId), kbId, docId, command, request);
    }

    private TokenClaims owned(Authentication authentication, String kbId) {
        TokenClaims claims = claims(authentication);
        knowledgeBaseService.assertOwned(claims.tenantId(), claims.sub(), kbId);
        return claims;
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }

    private String trace(String traceId) {
        return traceId == null || traceId.isBlank() ? UUID.randomUUID().toString() : traceId.trim();
    }

    private String safeRevision(String revision) {
        String value = revision == null ? "" : revision.trim();
        if (!value.matches("^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid RAG index revision");
        }
        return value;
    }
}
