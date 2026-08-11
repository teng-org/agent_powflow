package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Pageable;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TaskAuthorityServiceTest {
    private TaskRepository taskRepository;
    private TaskEventRepository eventRepository;
    private KnowledgeExecutionClient executionClient;
    private TaskBatchAggregateService batchAggregateService;
    private TaskAuthorityService service;

    @BeforeEach
    void setUp() {
        taskRepository = mock(TaskRepository.class);
        eventRepository = mock(TaskEventRepository.class);
        executionClient = mock(KnowledgeExecutionClient.class);
        batchAggregateService = mock(TaskBatchAggregateService.class);
        PlatformTransactionManager transactionManager = mock(PlatformTransactionManager.class);
        when(transactionManager.getTransaction(any(TransactionDefinition.class)))
                .thenAnswer(ignored -> new SimpleTransactionStatus());
        service = new TaskAuthorityService(
                taskRepository, eventRepository, executionClient,
                batchAggregateService, transactionManager
        );
    }

    @Test
    void listIsTenantAndKbScoped() {
        when(taskRepository.findByTenantIdAndKbIdOrderByUpdatedAtDesc(
                eq("tenant-a"), eq("kb-a"), any(Pageable.class)))
                .thenReturn(List.of(task("task-a", "tenant-a", "kb-a", false)));

        List<TaskDto.TaskView> result = service.list("tenant-a", "kb-a", 50);

        assertEquals(1, result.size());
        assertEquals("task-a", result.getFirst().task_id());
        assertEquals("tenant-a", result.getFirst().tenant_id());
    }

    @Test
    void batchCancellationCommitsControlStateAndRevokesEveryActiveTask() {
        TaskEntity parent = task("parent", "tenant-a", "kb-a", true);
        parent.setBatchId("batch-a");
        TaskEntity child = task("child", "tenant-a", "kb-a", false);
        child.setBatchId("batch-a");
        when(taskRepository.findByTaskIdAndTenantId("parent", "tenant-a")).thenReturn(Optional.of(parent));
        when(taskRepository.findByTenantIdAndBatchIdAndBatchIsParentFalseAndStatusIn(
                eq("tenant-a"), eq("batch-a"), any(Set.class))).thenReturn(List.of(child));
        when(executionClient.revokeTasks(List.of("parent", "child"))).thenReturn(true);

        TaskDto.CancelResponse result = service.cancel("tenant-a", "parent");

        assertEquals("CANCELLED", result.state());
        assertEquals(List.of("parent", "child"), result.cancelled_task_ids());
        assertTrue(result.revocation_dispatched());
        assertEquals("CONTROL", parent.getStateOwner());
        assertEquals("CONTROL", child.getStateOwner());
        verify(batchAggregateService).refresh("tenant-a", "batch-a");
        verify(eventRepository, times(2)).save(any(TaskEventEntity.class));
        verify(executionClient).revokeTasks(List.of("parent", "child"));
    }

    @Test
    void crossTenantCancellationFailsClosedBeforeBrokerRevocation() {
        when(taskRepository.findByTaskIdAndTenantId("task-a", "tenant-b")).thenReturn(Optional.empty());

        assertThrows(ResponseStatusException.class, () -> service.cancel("tenant-b", "task-a"));

        verify(executionClient, never()).revokeTasks(any());
    }

    @Test
    void internalEventAdvancesStateAndFinalizesProgress() {
        TaskEntity task = task("task-a", "tenant-a", "kb-a", false);
        TaskEventEntity persisted = mock(TaskEventEntity.class);
        when(persisted.getEventId()).thenReturn(1L);
        when(persisted.getTaskId()).thenReturn("task-a");
        when(persisted.getTenantId()).thenReturn("tenant-a");
        when(persisted.getKbId()).thenReturn("kb-a");
        when(persisted.getDocId()).thenReturn("doc-a");
        when(persisted.getEventType()).thenReturn("TASK_STATUS");
        when(persisted.getStage()).thenReturn("INDEXED");
        when(persisted.getPayload()).thenReturn(Map.of("status", "SUCCEEDED"));
        when(eventRepository.findByTenantIdAndEventKey("tenant-a", "event-1")).thenReturn(Optional.empty());
        when(taskRepository.lockByTaskIdAndTenantId("task-a", "tenant-a")).thenReturn(Optional.of(task));
        when(taskRepository.saveAndFlush(task)).thenReturn(task);
        when(eventRepository.saveAndFlush(any(TaskEventEntity.class))).thenReturn(persisted);

        TaskAuthorityService.InternalEventResult result = service.appendInternalEvent(
                "task-a", event("event-1", "kb-a", "SUCCEEDED", "INDEXED", 9, 10, 90)
        );

        assertEquals("SUCCEEDED", result.task().status());
        assertEquals(10, result.task().progress_current());
        assertEquals(100, result.task().progress_pct());
        verify(taskRepository).lockCommandScope("task-event\u001ftenant-a\u001fevent-1");
    }

    @Test
    void firstWorkerEventFillsBlankFilenameOnControlOwnedTask() {
        TaskEntity task = task("task-a", "tenant-a", "kb-a", false);
        task.setFilename("");
        TaskEventEntity persisted = mock(TaskEventEntity.class);
        when(persisted.getEventId()).thenReturn(1L);
        when(persisted.getTaskId()).thenReturn("task-a");
        when(persisted.getTenantId()).thenReturn("tenant-a");
        when(persisted.getKbId()).thenReturn("kb-a");
        when(persisted.getDocId()).thenReturn("doc-a");
        when(persisted.getEventType()).thenReturn("TASK_STATUS");
        when(persisted.getStage()).thenReturn("PARSING");
        when(persisted.getPayload()).thenReturn(Map.of("status", "RUNNING"));
        when(eventRepository.findByTenantIdAndEventKey("tenant-a", "event-filename")).thenReturn(Optional.empty());
        when(taskRepository.lockByTaskIdAndTenantId("task-a", "tenant-a")).thenReturn(Optional.of(task));
        when(taskRepository.saveAndFlush(task)).thenReturn(task);
        when(eventRepository.saveAndFlush(any(TaskEventEntity.class))).thenReturn(persisted);

        TaskAuthorityService.InternalEventResult result = service.appendInternalEvent(
                "task-a", event("event-filename", "kb-a", "RUNNING", "PARSING", 2, 10, 20)
        );

        assertEquals("sample.txt", result.task().filename());
    }

    @Test
    void terminalTaskRejectsStateMutationFromLaterEvent() {
        TaskEntity task = task("task-a", "tenant-a", "kb-a", false);
        task.setStatus("SUCCEEDED");
        task.setStage("INDEXED");
        task.setProgressCurrent(10);
        task.setProgressPct(100);
        when(eventRepository.findByTenantIdAndEventKey("tenant-a", "event-2")).thenReturn(Optional.empty());
        when(taskRepository.lockByTaskIdAndTenantId("task-a", "tenant-a")).thenReturn(Optional.of(task));

        assertThrows(ResponseStatusException.class, () -> service.appendInternalEvent(
                "task-a", event("event-2", "kb-a", "SUCCEEDED", "MUTATED", 10, 10, 100)
        ));

        verify(eventRepository, never()).saveAndFlush(any(TaskEventEntity.class));
    }

    @Test
    void duplicateInternalEventReturnsExistingFactWithoutAnotherWrite() {
        TaskEntity task = task("task-a", "tenant-a", "kb-a", false);
        TaskEventEntity duplicate = mock(TaskEventEntity.class);
        when(duplicate.getEventId()).thenReturn(7L);
        when(duplicate.getTaskId()).thenReturn("task-a");
        when(duplicate.getTenantId()).thenReturn("tenant-a");
        when(duplicate.getKbId()).thenReturn("kb-a");
        when(duplicate.getDocId()).thenReturn("doc-a");
        when(duplicate.getEventType()).thenReturn("TASK_STATUS");
        when(duplicate.getStage()).thenReturn("PARSING");
        when(duplicate.getPayload()).thenReturn(Map.of());
        when(eventRepository.findByTenantIdAndEventKey("tenant-a", "event-duplicate")).thenReturn(Optional.of(duplicate));
        when(taskRepository.findByTaskIdAndTenantId("task-a", "tenant-a")).thenReturn(Optional.of(task));

        TaskAuthorityService.InternalEventResult result = service.appendInternalEvent(
                "task-a", event("event-duplicate", "kb-a", "RUNNING", "PARSING", 2, 10, 20)
        );

        assertTrue(result.duplicate());
        verify(eventRepository, never()).saveAndFlush(any(TaskEventEntity.class));
    }

    private TaskDto.InternalTaskEventRequest event(
            String eventId, String kbId, String status, String stage,
            Integer current, Integer total, Integer pct
    ) {
        return new TaskDto.InternalTaskEventRequest(
                eventId, "tenant-a", kbId, "doc-a", "DOC_INGEST", "sample.txt",
                "TASK_STATUS", status, stage, current, total, pct, null,
                Map.of("status", status), "execution-plane"
        );
    }

    private TaskEntity task(String id, String tenant, String kb, boolean parent) {
        TaskEntity task = new TaskEntity();
        task.setTaskId(id);
        task.setTenantId(tenant);
        task.setKbId(kb);
        task.setDocId("doc-a");
        task.setTaskType("DOC_INGEST");
        task.setStatus("RUNNING");
        task.setStage("PARSING");
        task.setFilename("sample.txt");
        task.setBatchIsParent(parent);
        task.setProgressTotal(10);
        task.setProgressCurrent(2);
        task.setProgressPct(20);
        return task;
    }
}
