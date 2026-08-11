package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Map;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentUploadIntentServiceTest {
    private KnowledgeBaseRepository knowledgeBaseRepository;
    private TaskRepository taskRepository;
    private TaskEventRepository eventRepository;
    private TaskDispatchOutboxRepository outboxRepository;
    private TaskDispatchOutboxService outboxService;
    private DocumentUploadIntentService service;

    @BeforeEach
    void setUp() {
        knowledgeBaseRepository = mock(KnowledgeBaseRepository.class);
        taskRepository = mock(TaskRepository.class);
        eventRepository = mock(TaskEventRepository.class);
        outboxRepository = mock(TaskDispatchOutboxRepository.class);
        outboxService = mock(TaskDispatchOutboxService.class);
        service = new DocumentUploadIntentService(
                knowledgeBaseRepository, taskRepository, eventRepository, outboxRepository,
                outboxService, new TaskDispatchProperties(), new ObjectMapper()
        );
    }

    @Test
    void createPersistsControlTaskAndNonDispatchableUploadIntent() throws Exception {
        KnowledgeBaseEntity kb = new KnowledgeBaseEntity();
        kb.setTenantId("tenant-a");
        kb.setKbId("kb-a");
        kb.setStatus("ACTIVE");
        when(knowledgeBaseRepository.findByTenantIdAndKbId("tenant-a", "kb-a")).thenReturn(Optional.of(kb));
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getOutboxId()).thenReturn("outbox-1");
        when(outboxService.enqueueWithStatus(anyString(), anyString(), anyString(), anyString(), anyString()))
                .thenReturn(outbox);

        DocumentUploadIntentService.UploadIntent intent = service.create(
                "tenant-a", "actor-a", "trace-a", "kb-a", "sample.pdf",
                "UPLOAD_CLEAN_CHUNK_EMBED", "sentence", null, 500, 50, false
        );

        assertEquals("outbox-1", intent.outboxId());
        ArgumentCaptor<TaskEntity> taskCaptor = ArgumentCaptor.forClass(TaskEntity.class);
        verify(taskRepository).save(taskCaptor.capture());
        TaskEntity task = taskCaptor.getValue();
        assertEquals("CONTROL", task.getStateOwner());
        assertEquals("PENDING", task.getStatus());
        assertEquals("UPLOAD_PENDING", task.getStage());
        assertEquals(intent.taskId(), task.getTaskId());
        assertEquals(intent.docId(), task.getDocId());

        ArgumentCaptor<String> payload = ArgumentCaptor.forClass(String.class);
        verify(outboxService).enqueueWithStatus(
                eq("tenant-a"), eq(intent.taskId()), eq("DOC_INGEST_OBJECT"), payload.capture(), eq("WAITING_UPLOAD")
        );
        Map<?, ?> command = new ObjectMapper().readValue(payload.getValue(), Map.class);
        assertEquals("", command.get("content"));
        assertFalse(command.containsKey("content_object_key"));
        verify(eventRepository).save(argThat(event -> "TASK_CREATED".equals(event.getEventType())
                && "UPLOAD_PENDING".equals(event.getStage())));
    }

    @Test
    void activateAtomicallyPromotesOutboxAndTaskAfterStaging() {
        DocumentUploadIntentService.UploadIntent intent = new DocumentUploadIntentService.UploadIntent(
                "task-1", "doc-1", "outbox-1", "UPLOAD_CLEAN_CHUNK_EMBED"
        );
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getTenantId()).thenReturn("tenant-a");
        when(outbox.getTaskId()).thenReturn("task-1");
        when(outbox.getStatus()).thenReturn("WAITING_UPLOAD");
        when(outbox.getPayloadJson()).thenReturn("{\"task_id\":\"task-1\",\"content\":\"\"}");
        when(outbox.getOutboxId()).thenReturn("outbox-1");
        when(outboxRepository.lockByOutboxId("outbox-1")).thenReturn(Optional.of(outbox));
        TaskEntity task = task("task-1", "doc-1", "PENDING", "UPLOAD_PENDING");
        when(taskRepository.lockByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        service.activate("tenant-a", intent, Map.of(
                "process_mode", "UPLOAD_CLEAN_CHUNK_EMBED",
                "object_bucket", "documents",
                "content_object_key", "tenant/kb/doc/extracted.txt",
                "content_object_sha256", "a".repeat(64),
                "source_object_key", "tenant/kb/doc/source.pdf",
                "source_object_sha256", "b".repeat(64),
                "source_object_size_bytes", 123L,
                "extracted_object_size_bytes", 45L
        ));

        assertEquals("QUEUED", task.getStage());
        verify(outbox).setStatus("PENDING");
        verify(outbox).setPayloadJson(argThat(value -> value.contains("content_object_key") && value.contains("source.pdf")));
        verify(eventRepository).save(argThat(event -> "TASK_UPLOAD_STAGED".equals(event.getEventType())));
    }

    @Test
    void completeNonIndexedUploadPersistsSuccessFactWithoutDispatch() {
        DocumentUploadIntentService.UploadIntent intent = new DocumentUploadIntentService.UploadIntent(
                "task-1", "doc-1", "outbox-1", "UPLOAD_AND_CLEAN"
        );
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getTenantId()).thenReturn("tenant-a");
        when(outbox.getTaskId()).thenReturn("task-1");
        when(outbox.getStatus()).thenReturn("WAITING_UPLOAD");
        when(outbox.getPayloadJson()).thenReturn("{\"task_id\":\"task-1\",\"process_mode\":\"UPLOAD_AND_CLEAN\"}");
        when(outbox.getOutboxId()).thenReturn("outbox-1");
        when(outboxRepository.lockByOutboxId("outbox-1")).thenReturn(Optional.of(outbox));
        TaskEntity task = task("task-1", "doc-1", "PENDING", "UPLOAD_PENDING");
        task.setTaskType("DOC_UPLOAD");
        when(taskRepository.lockByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        service.completeWithoutDispatch("tenant-a", intent, Map.of(
                "process_mode", "UPLOAD_AND_CLEAN",
                "object_bucket", "documents",
                "content_object_key", "tenant/kb/doc/extracted.txt",
                "content_object_sha256", "a".repeat(64),
                "source_object_key", "tenant/kb/doc/source.pdf",
                "source_object_sha256", "b".repeat(64),
                "source_object_size_bytes", 123L,
                "extracted_object_size_bytes", 45L
        ));

        assertEquals("SUCCEEDED", task.getStatus());
        assertEquals("CLEANED", task.getStage());
        assertEquals(100, task.getProgressPct());
        verify(outbox).setStatus("COMPLETED");
        verify(eventRepository).save(argThat(event -> "TASK_UPLOAD_COMPLETED".equals(event.getEventType())
                && "CLEANED".equals(event.getStage())));
    }

    @Test
    void failClosesWaitingIntentAndMakesTaskTerminal() {
        DocumentUploadIntentService.UploadIntent intent = new DocumentUploadIntentService.UploadIntent(
                "task-1", "doc-1", "outbox-1", "UPLOAD_CLEAN_CHUNK_EMBED"
        );
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getTenantId()).thenReturn("tenant-a");
        when(outbox.getStatus()).thenReturn("WAITING_UPLOAD");
        when(outboxRepository.lockByOutboxId("outbox-1")).thenReturn(Optional.of(outbox));
        TaskEntity task = task("task-1", "doc-1", "PENDING", "UPLOAD_PENDING");
        when(taskRepository.lockByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        assertTrue(service.fail("tenant-a", intent, "UPLOAD_FAILED", "parse rejected"));

        assertEquals("FAILED", task.getStatus());
        assertEquals("UPLOAD_FAILED", task.getStage());
        verify(outbox).setStatus("CLEANUP_PENDING");
        verify(eventRepository).save(argThat(event -> "TASK_UPLOAD_FAILED".equals(event.getEventType())));
    }

    @Test
    void cleanupIntentRemainsDurableUntilRemoteCleanupSucceeds() {
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getOutboxId()).thenReturn("outbox-1");
        when(outbox.getTaskId()).thenReturn("task-1");
        when(outbox.getTenantId()).thenReturn("tenant-a");
        when(outbox.getStatus()).thenReturn("CLEANUP_PENDING");
        TaskEntity task = task("task-1", "doc-1", "FAILED", "UPLOAD_FAILED");
        when(outboxRepository.lockPendingUploadCleanup(50)).thenReturn(List.of(outbox));
        when(taskRepository.findByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        List<DocumentUploadIntentService.CleanupTarget> pending = service.pendingCleanup();
        assertEquals(1, pending.size());
        assertEquals("doc-1", pending.getFirst().docId());

        when(outboxRepository.findByTaskId("task-1")).thenReturn(Optional.of(outbox));
        when(outboxRepository.lockByOutboxId("outbox-1")).thenReturn(Optional.of(outbox));
        service.markCleanupComplete("tenant-a", "task-1");
        verify(outbox).setStatus("CANCELLED");
    }

    @Test
    void staleNonIndexIntentBecomesRecoverableCleanupTarget() {
        TaskDispatchOutboxEntity outbox = mock(TaskDispatchOutboxEntity.class);
        when(outbox.getTaskId()).thenReturn("task-upload");
        when(outbox.getTenantId()).thenReturn("tenant-a");
        TaskEntity task = task("task-upload", "doc-upload", "PENDING", "UPLOAD_PENDING");
        task.setTaskType("DOC_UPLOAD");
        when(outboxRepository.lockExpiredUploadIntents(any(), eq(50))).thenReturn(List.of(outbox));
        when(taskRepository.lockByTaskIdAndTenantId("task-upload", "tenant-a")).thenReturn(Optional.of(task));

        List<DocumentUploadIntentService.CleanupTarget> expired = service.expireStale();

        assertEquals(1, expired.size());
        assertEquals("doc-upload", expired.getFirst().docId());
        assertEquals("FAILED", task.getStatus());
        assertEquals("UPLOAD_TIMEOUT", task.getStage());
        verify(outbox).setStatus("CLEANUP_PENDING");
        verify(eventRepository).save(argThat(event -> "TASK_UPLOAD_FAILED".equals(event.getEventType())
                && "UPLOAD_TIMEOUT".equals(event.getStage())));
    }

    private TaskEntity task(String taskId, String docId, String status, String stage) {
        TaskEntity task = new TaskEntity();
        task.setTaskId(taskId);
        task.setTenantId("tenant-a");
        task.setKbId("kb-a");
        task.setDocId(docId);
        task.setTaskType("DOC_INGEST");
        task.setStatus(status);
        task.setStage(stage);
        task.setStateOwner("CONTROL");
        return task;
    }
}
