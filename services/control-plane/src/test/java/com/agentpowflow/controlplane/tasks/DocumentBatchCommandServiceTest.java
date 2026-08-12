package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DocumentBatchCommandServiceTest {
    private KnowledgeBaseRepository kbRepository;
    private KnowledgeExecutionClient executionClient;
    private TaskRepository taskRepository;
    private TaskEventRepository eventRepository;
    private TaskBatchRepository batchRepository;
    private TaskDispatchOutboxService outboxService;
    private DocumentBatchCommandService service;

    @BeforeEach
    void setUp() {
        kbRepository = mock(KnowledgeBaseRepository.class);
        executionClient = mock(KnowledgeExecutionClient.class);
        taskRepository = mock(TaskRepository.class);
        eventRepository = mock(TaskEventRepository.class);
        batchRepository = mock(TaskBatchRepository.class);
        outboxService = mock(TaskDispatchOutboxService.class);
        PlatformTransactionManager transactionManager = mock(PlatformTransactionManager.class);
        when(transactionManager.getTransaction(any(TransactionDefinition.class)))
                .thenAnswer(ignored -> new SimpleTransactionStatus());
        when(taskRepository.save(any(TaskEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(batchRepository.save(any(TaskBatchEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(kbRepository.findByTenantIdAndKbId(eq("tenant-a"), anyString()))
                .thenAnswer(invocation -> Optional.of(kb(invocation.getArgument(1))));
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                anyString(), anyString(), anyString(), any())).thenReturn(Optional.empty());
        when(executionClient.describeDocuments("tenant-a", "kb-a", List.of("doc-a", "doc-b")))
                .thenReturn(Map.of(
                        "status", "ok",
                        "items", List.of(
                                Map.of("doc_id", "doc-a", "filename", "a.txt", "chunk_count", 0),
                                Map.of("doc_id", "doc-b", "filename", "b.txt", "chunk_count", 3)
                        ),
                        "missing_doc_ids", List.of()
                ));
        service = new DocumentBatchCommandService(
                kbRepository, executionClient, taskRepository, eventRepository, batchRepository,
                outboxService, new ObjectMapper(), transactionManager
        );
    }

    @Test
    void createsControlOwnedParentChildrenAndOneOutboxPerChild() {
        TaskDto.BatchDocumentsRequest request = new TaskDto.BatchDocumentsRequest(
                "chunk", List.of("doc-a", "doc-b"), "sentence", null, 100, 0,
                null, null, null, "authority batch"
        );

        TaskDto.BatchAcceptedResponse response = service.submit(
                "tenant-a", "actor-a", "trace-a", "kb-a", request
        );

        assertEquals(2, response.accepted_count());
        assertEquals("authority batch", response.batch_name());
        verify(batchRepository).save(any(TaskBatchEntity.class));
        ArgumentCaptor<TaskEntity> tasks = ArgumentCaptor.forClass(TaskEntity.class);
        verify(taskRepository, times(3)).save(tasks.capture());
        List<String> childTypes = tasks.getAllValues().stream()
                .filter(task -> !task.isBatchIsParent()).map(TaskEntity::getTaskType).toList();
        assertEquals(List.of("DOC_CHUNK", "DOC_RECHUNK"), childTypes);
        verify(eventRepository, times(3)).save(any(TaskEventEntity.class));
        verify(outboxService, times(2)).enqueue(
                eq("tenant-a"), anyString(), eq("DOCUMENT_BATCH_ITEM"),
                org.mockito.ArgumentMatchers.argThat(json -> json.contains("\"chunk_overlap\":0")
                        && json.contains("\"batch_id\":"))
        );
    }

    @Test
    void acceptsPageChunkingForBatchRechunk() {
        TaskDto.BatchDocumentsRequest request = new TaskDto.BatchDocumentsRequest(
                "chunk", List.of("doc-a", "doc-b"), "page", null, 500, 50,
                null, null, null, "page batch"
        );

        service.submit("tenant-a", "actor-a", "trace-page", "kb-a", request);

        verify(outboxService, times(2)).enqueue(
                eq("tenant-a"), anyString(), eq("DOCUMENT_BATCH_ITEM"),
                org.mockito.ArgumentMatchers.argThat(json -> json.contains("\"chunk_method\":\"page\""))
        );
    }

    @Test
    void rejectsWholeBatchWhenAnyDocumentAlreadyHasAnActiveCommand() {
        TaskEntity active = new TaskEntity();
        active.setTaskId("active-task");
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                eq("tenant-a"), eq("kb-a"), eq("doc-b"), any())).thenReturn(Optional.of(active));
        TaskDto.BatchDocumentsRequest request = new TaskDto.BatchDocumentsRequest(
                "delete", List.of("doc-a", "doc-b"), null, null, null, null,
                null, null, null, "delete batch"
        );

        ResponseStatusException conflict = assertThrows(ResponseStatusException.class, () ->
                service.submit("tenant-a", "actor-a", "trace-a", "kb-a", request)
        );

        assertEquals(409, conflict.getStatusCode().value());
        verify(batchRepository, never()).save(any());
        verify(outboxService, never()).enqueue(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void rejectsMissingDocumentsBeforeOpeningBatchTransaction() {
        when(executionClient.describeDocuments("tenant-a", "kb-a", List.of("doc-a", "doc-b")))
                .thenReturn(Map.of(
                        "status", "ok", "items", List.of(Map.of("doc_id", "doc-a", "filename", "a.txt")),
                        "missing_doc_ids", List.of("doc-b")
                ));
        TaskDto.BatchDocumentsRequest request = new TaskDto.BatchDocumentsRequest(
                "summarize", List.of("doc-a", "doc-b"), null, null, null, null,
                null, null, null, "summary batch"
        );

        ResponseStatusException missing = assertThrows(ResponseStatusException.class, () ->
                service.submit("tenant-a", "actor-a", "trace-a", "kb-a", request)
        );

        assertEquals(404, missing.getStatusCode().value());
        verify(taskRepository, never()).save(any());
    }

    private KnowledgeBaseEntity kb(String id) {
        KnowledgeBaseEntity kb = new KnowledgeBaseEntity();
        kb.setTenantId("tenant-a");
        kb.setKbId(id);
        kb.setName("KB " + id);
        kb.setStatus("ACTIVE");
        return kb;
    }
}
