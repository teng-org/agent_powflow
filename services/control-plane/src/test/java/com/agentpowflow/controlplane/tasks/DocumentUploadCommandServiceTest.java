package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentUploadCommandServiceTest {
    private DocumentUploadIntentService intentService;
    private KnowledgeExecutionClient executionClient;
    private DocumentUploadCommandService service;

    @BeforeEach
    void setUp() {
        intentService = mock(DocumentUploadIntentService.class);
        executionClient = mock(KnowledgeExecutionClient.class);
        service = new DocumentUploadCommandService(intentService, executionClient, new TaskDispatchProperties());
    }

    @Test
    void indexedUploadStagesThenActivatesControlIntent() {
        var file = new MockMultipartFile("file", "sample.txt", "text/plain", "hello".getBytes());
        var intent = new DocumentUploadIntentService.UploadIntent(
                "task-1", "doc-1", "outbox-1", "UPLOAD_CLEAN_CHUNK_EMBED"
        );
        when(intentService.create(anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), anyString(), nullable(String.class), anyInt(), anyInt(), anyBoolean())).thenReturn(intent);
        Map<String, Object> staged = Map.of(
                "status", "staged", "process_mode", "UPLOAD_CLEAN_CHUNK_EMBED", "object_bucket", "documents",
                "content_object_key", "text-key", "content_object_sha256", "a".repeat(64),
                "source_object_key", "source-key", "source_object_sha256", "b".repeat(64),
                "source_object_size_bytes", 5, "extracted_object_size_bytes", 5
        );
        when(executionClient.stageDocument(anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), nullable(String.class), anyInt(), anyInt(), any())).thenReturn(staged);

        TaskDto.UploadResponse response = service.upload(
                "tenant-a", "actor-a", "trace-a", "kb-a", List.of(file),
                "UPLOAD_CLEAN_CHUNK_EMBED", "sentence", null, 500, 50, false
        );

        assertEquals("accepted", response.status());
        assertEquals("task-1", response.accepted().getFirst().task_id());
        verify(intentService).activate("tenant-a", intent, staged);
        verify(intentService, never()).fail(anyString(), any(), anyString(), anyString());
    }

    @Test
    void stagingFailureClosesIntentAndRequestsExactCleanup() {
        var file = new MockMultipartFile("file", "broken.pdf", "application/pdf", "broken".getBytes());
        var intent = new DocumentUploadIntentService.UploadIntent(
                "task-1", "doc-1", "outbox-1", "UPLOAD_CLEAN_CHUNK_EMBED"
        );
        when(intentService.create(anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), anyString(), nullable(String.class), anyInt(), anyInt(), anyBoolean())).thenReturn(intent);
        when(executionClient.stageDocument(anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), nullable(String.class), anyInt(), anyInt(), any())).thenThrow(new IllegalStateException("parse rejected"));
        when(intentService.fail("tenant-a", intent, "UPLOAD_FAILED", "parse rejected")).thenReturn(true);

        TaskDto.UploadResponse response = service.upload(
                "tenant-a", "actor-a", "trace-a", "kb-a", List.of(file),
                "UPLOAD_CLEAN_CHUNK_EMBED", "sentence", null, 500, 50, false
        );

        assertEquals("rejected", response.status());
        assertEquals(1, response.skipped_count());
        verify(executionClient).cleanupStagedDocument("tenant-a", "kb-a", "doc-1", "task-1");
    }

    @Test
    void nonIndexedUploadUsesDurableIntentAndCompletesWithoutDispatch() {
        var file = new MockMultipartFile("file", "sample.txt", "text/plain", "hello".getBytes());
        var intent = new DocumentUploadIntentService.UploadIntent(
                "task-upload", "doc-upload", "outbox-upload", "UPLOAD_ONLY"
        );
        when(intentService.create(anyString(), anyString(), anyString(), anyString(), anyString(),
                eq("UPLOAD_ONLY"), anyString(), nullable(String.class), anyInt(), anyInt(), eq(false))).thenReturn(intent);
        Map<String, Object> staged = Map.of(
                "status", "staged", "process_mode", "UPLOAD_ONLY", "object_bucket", "documents",
                "content_object_key", "text-key", "content_object_sha256", "a".repeat(64),
                "source_object_key", "source-key", "source_object_sha256", "b".repeat(64),
                "source_object_size_bytes", 5, "extracted_object_size_bytes", 5
        );
        when(executionClient.stageDocument(anyString(), anyString(), anyString(), anyString(), eq("UPLOAD_ONLY"),
                anyString(), nullable(String.class), anyInt(), anyInt(), any())).thenReturn(staged);

        TaskDto.UploadResponse response = service.upload(
                "tenant-a", "actor-a", "trace-a", "kb-a", List.of(file),
                "UPLOAD_ONLY", "sentence", null, 500, 50, true
        );

        assertEquals("accepted", response.status());
        assertEquals("task-upload", response.accepted().getFirst().task_id());
        verify(intentService).completeWithoutDispatch("tenant-a", intent, staged);
        verify(intentService, never()).activate(anyString(), any(), any());
    }

    @Test
    void nonIndexedCompletionFailureBecomesDurableCleanupAndDeletesExactDocument() {
        var file = new MockMultipartFile("file", "sample.txt", "text/plain", "hello".getBytes());
        var intent = new DocumentUploadIntentService.UploadIntent(
                "task-upload", "doc-upload", "outbox-upload", "UPLOAD_AND_CLEAN"
        );
        when(intentService.create(anyString(), anyString(), anyString(), anyString(), anyString(),
                eq("UPLOAD_AND_CLEAN"), anyString(), nullable(String.class), anyInt(), anyInt(), eq(false))).thenReturn(intent);
        Map<String, Object> staged = Map.of(
                "status", "staged", "process_mode", "UPLOAD_AND_CLEAN",
                "object_bucket", "documents", "content_object_key", "text-key"
        );
        when(executionClient.stageDocument(anyString(), anyString(), anyString(), anyString(),
                eq("UPLOAD_AND_CLEAN"), anyString(), nullable(String.class), anyInt(), anyInt(), any())).thenReturn(staged);
        doThrow(new IllegalStateException("completion transaction failed"))
                .when(intentService).completeWithoutDispatch("tenant-a", intent, staged);
        when(intentService.fail("tenant-a", intent, "UPLOAD_FAILED", "completion transaction failed")).thenReturn(true);
        when(executionClient.cleanupStagedDocument("tenant-a", "kb-a", "doc-upload", "task-upload")).thenReturn(true);

        TaskDto.UploadResponse response = service.upload(
                "tenant-a", "actor-a", "trace-a", "kb-a", List.of(file),
                "UPLOAD_AND_CLEAN", "sentence", null, 500, 50, false
        );

        assertEquals("rejected", response.status());
        verify(intentService).markCleanupComplete("tenant-a", "task-upload");
    }
}
