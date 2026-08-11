package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

@Service
public class DocumentUploadCommandService {
    private static final Set<String> PROCESS_MODES = Set.of(
            "UPLOAD_ONLY", "UPLOAD_AND_CLEAN", "UPLOAD_CLEAN_CHUNK_EMBED"
    );
    private static final Set<String> CHUNK_METHODS = Set.of(
            "fixed", "sentence", "paragraph", "markdown", "delimiter"
    );
    private final DocumentUploadIntentService intentService;
    private final KnowledgeExecutionClient executionClient;
    private final TaskDispatchProperties properties;

    public DocumentUploadCommandService(
            DocumentUploadIntentService intentService,
            KnowledgeExecutionClient executionClient,
            TaskDispatchProperties properties
    ) {
        this.intentService = intentService;
        this.executionClient = executionClient;
        this.properties = properties;
    }

    public TaskDto.UploadResponse upload(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            List<MultipartFile> files,
            String processMode,
            String chunkMethod,
            String chunkDelimiter,
            int chunkSize,
            int chunkOverlap,
            boolean autoSummary
    ) {
        String mode = normalizeMode(processMode);
        String method = normalizeChunkMethod(chunkMethod);
        String delimiter = chunkDelimiter == null || chunkDelimiter.isBlank() ? null : chunkDelimiter;
        validateChunking(method, delimiter, chunkSize, chunkOverlap);
        intentService.validateWritable(tenantId, kbId);

        int maxFiles = Math.max(1, Math.min(properties.getMaxUploadFiles(), 200));
        long maxFileBytes = Math.max(1024, properties.getMaxUploadFileBytes());
        long maxBatchBytes = Math.max(maxFileBytes, properties.getMaxUploadBatchBytes());
        List<TaskDto.UploadAcceptedItem> accepted = new ArrayList<>();
        List<TaskDto.UploadSkippedItem> skipped = new ArrayList<>();
        long consumed = 0;
        List<MultipartFile> safeFiles = files == null ? List.of() : files;
        for (int index = 0; index < safeFiles.size(); index++) {
            MultipartFile file = safeFiles.get(index);
            String filename = file == null || file.getOriginalFilename() == null
                    ? "" : file.getOriginalFilename().trim();
            if (index >= maxFiles) {
                skipped.add(new TaskDto.UploadSkippedItem(filename, "too many files (> " + maxFiles + ")"));
                continue;
            }
            if (filename.isBlank() || filename.length() > 512) {
                skipped.add(new TaskDto.UploadSkippedItem(filename, "missing or invalid filename"));
                continue;
            }
            long size = Math.max(0, file.getSize());
            if (size == 0) {
                skipped.add(new TaskDto.UploadSkippedItem(filename, "empty file"));
                continue;
            }
            if (size > maxFileBytes) {
                skipped.add(new TaskDto.UploadSkippedItem(filename, "file exceeds Control upload limit"));
                continue;
            }
            if (consumed + size > maxBatchBytes) {
                skipped.add(new TaskDto.UploadSkippedItem(filename, "upload batch byte limit reached"));
                continue;
            }
            consumed += size;
            if ("UPLOAD_CLEAN_CHUNK_EMBED".equals(mode)) {
                uploadIndexed(
                        tenantId, actorId, traceId, kbId, file, filename, method, delimiter,
                        chunkSize, chunkOverlap, autoSummary, accepted, skipped
                );
            } else {
                uploadNonIndexed(
                        tenantId, actorId, traceId, kbId, file, filename, mode, method, delimiter,
                        chunkSize, chunkOverlap, accepted, skipped
                );
            }
        }
        return new TaskDto.UploadResponse(
                accepted.isEmpty() ? "rejected" : "accepted",
                kbId,
                accepted.size(),
                skipped.size(),
                maxFiles,
                maxFileBytes,
                maxBatchBytes,
                accepted,
                skipped,
                traceId
        );
    }

    private void uploadIndexed(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            MultipartFile file,
            String filename,
            String method,
            String delimiter,
            int chunkSize,
            int chunkOverlap,
            boolean autoSummary,
            List<TaskDto.UploadAcceptedItem> accepted,
            List<TaskDto.UploadSkippedItem> skipped
    ) {
        DocumentUploadIntentService.UploadIntent intent = intentService.create(
                tenantId, actorId, traceId, kbId, filename, "UPLOAD_CLEAN_CHUNK_EMBED", method, delimiter,
                chunkSize, chunkOverlap, autoSummary
        );
        try {
            Map<String, Object> staged = executionClient.stageDocument(
                    tenantId, kbId, intent.docId(), intent.taskId(), "UPLOAD_CLEAN_CHUNK_EMBED",
                    method, delimiter, chunkSize, chunkOverlap, file
            );
            intentService.activate(tenantId, intent, staged);
            accepted.add(new TaskDto.UploadAcceptedItem(filename, intent.docId(), intent.taskId(), "UPLOAD_CLEAN_CHUNK_EMBED"));
        } catch (RuntimeException exception) {
            boolean closed = intentService.fail(tenantId, intent, "UPLOAD_FAILED", rootMessage(exception));
            if (closed && executionClient.cleanupStagedDocument(tenantId, kbId, intent.docId(), intent.taskId())) {
                intentService.markCleanupComplete(tenantId, intent.taskId());
            }
            skipped.add(new TaskDto.UploadSkippedItem(filename, rootMessage(exception)));
        }
    }

    private void uploadNonIndexed(
            String tenantId,
            String actorId,
            String traceId,
            String kbId,
            MultipartFile file,
            String filename,
            String mode,
            String method,
            String delimiter,
            int chunkSize,
            int chunkOverlap,
            List<TaskDto.UploadAcceptedItem> accepted,
            List<TaskDto.UploadSkippedItem> skipped
    ) {
        DocumentUploadIntentService.UploadIntent intent = intentService.create(
                tenantId, actorId, traceId, kbId, filename, mode, method, delimiter,
                chunkSize, chunkOverlap, false
        );
        try {
            Map<String, Object> staged = executionClient.stageDocument(
                    tenantId, kbId, intent.docId(), intent.taskId(), mode,
                    method, delimiter, chunkSize, chunkOverlap, file
            );
            intentService.completeWithoutDispatch(tenantId, intent, staged);
            accepted.add(new TaskDto.UploadAcceptedItem(filename, intent.docId(), intent.taskId(), mode));
        } catch (RuntimeException exception) {
            boolean closed = intentService.fail(tenantId, intent, "UPLOAD_FAILED", rootMessage(exception));
            if (closed && executionClient.cleanupStagedDocument(
                    tenantId, kbId, intent.docId(), intent.taskId()
            )) {
                intentService.markCleanupComplete(tenantId, intent.taskId());
            }
            skipped.add(new TaskDto.UploadSkippedItem(filename, rootMessage(exception)));
        }
    }

    private String normalizeMode(String value) {
        String mode = value == null || value.isBlank()
                ? "UPLOAD_CLEAN_CHUNK_EMBED" : value.trim().toUpperCase(Locale.ROOT);
        if (!PROCESS_MODES.contains(mode)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid process_mode");
        return mode;
    }

    private String normalizeChunkMethod(String value) {
        String method = value == null || value.isBlank() ? "sentence" : value.trim().toLowerCase(Locale.ROOT);
        if (!CHUNK_METHODS.contains(method)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid chunk_method");
        return method;
    }

    private void validateChunking(String method, String delimiter, int chunkSize, int chunkOverlap) {
        if ("delimiter".equals(method) && delimiter == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_delimiter is required when chunk_method=delimiter");
        }
        if (chunkSize < 100 || chunkSize > 2000) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_size must be between 100 and 2000");
        if (chunkOverlap < 0 || chunkOverlap > 500 || chunkOverlap >= chunkSize) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "chunk_overlap must be between 0 and 500 and smaller than chunk_size");
        }
    }

    private String rootMessage(Throwable throwable) {
        Throwable current = throwable;
        while (current.getCause() != null && current.getCause() != current) current = current.getCause();
        String message = current.getMessage();
        if (message == null || message.isBlank()) message = throwable.getMessage();
        if (message == null || message.isBlank()) message = "upload staging failed";
        return message.length() <= 1000 ? message : message.substring(0, 1000);
    }
}
