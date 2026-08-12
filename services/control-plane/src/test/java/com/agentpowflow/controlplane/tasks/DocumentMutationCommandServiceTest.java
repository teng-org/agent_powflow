package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeBaseEntity;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentMutationCommandServiceTest {
    private KnowledgeBaseRepository kbRepository;
    private TaskRepository taskRepository;
    private TaskEventRepository eventRepository;
    private TaskDispatchOutboxService outboxService;
    private DocumentMutationCommandService service;

    @BeforeEach
    void setUp() {
        kbRepository = mock(KnowledgeBaseRepository.class);
        taskRepository = mock(TaskRepository.class);
        eventRepository = mock(TaskEventRepository.class);
        outboxService = mock(TaskDispatchOutboxService.class);
        when(taskRepository.save(any(TaskEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(kbRepository.findByTenantIdAndKbId(eq("tenant-a"), anyString()))
                .thenAnswer(invocation -> Optional.of(kb(invocation.getArgument(1))));
        service = new DocumentMutationCommandService(
                kbRepository, taskRepository, eventRepository, outboxService, new ObjectMapper()
        );
    }

    @Test
    void createsControlTaskEventAndOutboxForDocumentSummary() {
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                anyString(), anyString(), anyString(), anySet())).thenReturn(Optional.empty());

        TaskDto.DocumentCommandAcceptedResponse response = service.submitDocument(
                "tenant-a", "actor-a", "trace-a", "kb-a", "doc-a", "summarize", null
        );

        assertEquals("DOC_SUMMARIZE", response.task_type());
        assertFalse(response.deduplicated());
        ArgumentCaptor<TaskEntity> taskCaptor = ArgumentCaptor.forClass(TaskEntity.class);
        verify(taskRepository).save(taskCaptor.capture());
        assertEquals("CONTROL", taskCaptor.getValue().getStateOwner());
        assertEquals("QUEUED", taskCaptor.getValue().getStage());
        verify(eventRepository).save(argThat(event -> "TASK_CREATED".equals(event.getEventType())));
        verify(outboxService).enqueue(
                eq("tenant-a"), eq(response.task_id()), eq("DOCUMENT_COMMAND"),
                argThat(json -> json.contains("\"task_type\":\"DOC_SUMMARIZE\""))
        );
    }

    @Test
    void preservesZeroOverlapAndValidatesMoveTarget() {
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                anyString(), anyString(), anyString(), anySet())).thenReturn(Optional.empty());
        var chunk = new TaskDto.DocumentCommandRequest("sentence", null, 100, 0, null, null, null);

        TaskDto.DocumentCommandAcceptedResponse chunkResponse = service.submitDocument(
                "tenant-a", "actor-a", "trace-a", "kb-a", "doc-a", "chunk", chunk
        );
        verify(outboxService).enqueue(
                eq("tenant-a"), eq(chunkResponse.task_id()), eq("DOCUMENT_COMMAND"),
                argThat(json -> json.contains("\"chunk_overlap\":0"))
        );

        reset(outboxService);
        var move = new TaskDto.DocumentCommandRequest(null, null, null, null, "kb-b", null, null);
        TaskDto.DocumentCommandAcceptedResponse moveResponse = service.submitDocument(
                "tenant-a", "actor-a", "trace-b", "kb-a", "doc-b", "move", move
        );
        verify(kbRepository).findByTenantIdAndKbId("tenant-a", "kb-b");
        verify(outboxService).enqueue(
                eq("tenant-a"), eq(moveResponse.task_id()), eq("DOCUMENT_COMMAND"),
                argThat(json -> json.contains("\"target_kb_id\":\"kb-b\""))
        );
    }

    @Test
    void acceptsPageChunkingForDocumentRechunk() {
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                anyString(), anyString(), anyString(), anySet())).thenReturn(Optional.empty());
        var pageChunk = new TaskDto.DocumentCommandRequest("page", null, 500, 50, null, null, null);

        TaskDto.DocumentCommandAcceptedResponse response = service.submitDocument(
                "tenant-a", "actor-a", "trace-page", "kb-a", "doc-page", "chunk", pageChunk
        );

        verify(outboxService).enqueue(
                eq("tenant-a"), eq(response.task_id()), eq("DOCUMENT_COMMAND"),
                argThat(json -> json.contains("\"chunk_method\":\"page\""))
        );
    }

    @Test
    void deduplicatesSameCommandAndRejectsCompetingMutation() {
        TaskEntity active = new TaskEntity();
        active.setTaskId("active-task");
        active.setTenantId("tenant-a");
        active.setKbId("kb-a");
        active.setDocId("doc-a");
        active.setTaskType("DOC_TAG");
        active.setStatus("PENDING");
        active.setStage("QUEUED");
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndStatusInOrderByCreatedAtAsc(
                eq("tenant-a"), eq("kb-a"), eq("doc-a"), eq(Set.of("PENDING", "RUNNING", "CANCELLING"))))
                .thenReturn(Optional.of(active));
        var tags = new TaskDto.DocumentCommandRequest(null, null, null, null, null, List.of("reviewed"), "replace");

        TaskDto.DocumentCommandAcceptedResponse duplicate = service.submitDocument(
                "tenant-a", "actor-a", "trace-a", "kb-a", "doc-a", "tag", tags
        );
        assertTrue(duplicate.deduplicated());
        assertEquals("active-task", duplicate.task_id());
        verify(outboxService, never()).enqueue(anyString(), anyString(), anyString(), anyString());

        ResponseStatusException conflict = assertThrows(ResponseStatusException.class, () -> service.submitDocument(
                "tenant-a", "actor-a", "trace-a", "kb-a", "doc-a", "delete", null
        ));
        assertEquals(409, conflict.getStatusCode().value());
    }

    @Test
    void createsKnowledgeBaseSummaryWithoutDocumentIdentity() {
        when(taskRepository.findFirstByTenantIdAndKbIdAndDocIdAndTaskTypeAndStatusInOrderByCreatedAtAsc(
                anyString(), anyString(), anyString(), anyString(), anySet())).thenReturn(Optional.empty());

        TaskDto.DocumentCommandAcceptedResponse response = service.summarizeKnowledgeBase(
                "tenant-a", "actor-a", "trace-a", "kb-a"
        );

        assertEquals("KB_SUMMARIZE", response.task_type());
        assertEquals("", response.doc_id());
        verify(outboxService).enqueue(
                eq("tenant-a"), eq(response.task_id()), eq("KB_SUMMARIZE"),
                argThat(json -> json.contains("\"kb_name\":\"KB kb-a\""))
        );
    }

    private KnowledgeBaseEntity kb(String kbId) {
        KnowledgeBaseEntity entity = new KnowledgeBaseEntity();
        entity.setTenantId("tenant-a");
        entity.setKbId(kbId);
        entity.setName("KB " + kbId);
        entity.setStatus("ACTIVE");
        entity.setFrozen(false);
        return entity;
    }
}
