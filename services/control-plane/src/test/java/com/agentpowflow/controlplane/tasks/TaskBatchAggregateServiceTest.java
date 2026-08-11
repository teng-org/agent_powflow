package com.agentpowflow.controlplane.tasks;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TaskBatchAggregateServiceTest {
    @Test
    void aggregatesPartialFailureIntoParentAndBatchTerminalState() {
        TaskBatchRepository batchRepository = mock(TaskBatchRepository.class);
        TaskRepository taskRepository = mock(TaskRepository.class);
        TaskEventRepository eventRepository = mock(TaskEventRepository.class);
        TaskBatchAggregateService service = new TaskBatchAggregateService(
                batchRepository, taskRepository, eventRepository
        );
        TaskBatchEntity batch = new TaskBatchEntity();
        batch.setBatchId("batch-a");
        batch.setTenantId("tenant-a");
        batch.setKbId("kb-a");
        batch.setParentTaskId("parent-a");
        batch.setTotalItems(2);
        batch.setRunningItems(2);
        batch.setStatus("RUNNING");
        TaskEntity parent = task("parent-a", "", "RUNNING");
        parent.setBatchId("batch-a");
        parent.setBatchIsParent(true);
        TaskEntity succeeded = task("child-a", "doc-a", "SUCCEEDED");
        TaskEntity failed = task("child-b", "doc-b", "FAILED");
        when(batchRepository.lockByBatchIdAndTenantId("batch-a", "tenant-a")).thenReturn(Optional.of(batch));
        when(taskRepository.findByTenantIdAndBatchIdAndBatchIsParentFalse("tenant-a", "batch-a"))
                .thenReturn(List.of(succeeded, failed));
        when(taskRepository.lockByTaskIdAndTenantId("parent-a", "tenant-a")).thenReturn(Optional.of(parent));
        when(batchRepository.save(any(TaskBatchEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(batchRepository.saveAndFlush(any(TaskBatchEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(eventRepository.findByTenantIdAndEventKey(any(), any())).thenReturn(Optional.empty());

        TaskBatchEntity result = service.refresh("tenant-a", "batch-a");

        assertEquals("FAILED", result.getStatus());
        assertEquals(1, result.getSucceededItems());
        assertEquals(1, result.getFailedItems());
        assertEquals("FAILED", parent.getStatus());
        assertEquals(100, parent.getProgressPct());
        verify(eventRepository).save(any(TaskEventEntity.class));
    }

    @Test
    void cancelledChildrenProduceCancelledBatchInsteadOfFalseSuccess() {
        TaskBatchRepository batchRepository = mock(TaskBatchRepository.class);
        TaskRepository taskRepository = mock(TaskRepository.class);
        TaskEventRepository eventRepository = mock(TaskEventRepository.class);
        TaskBatchAggregateService service = new TaskBatchAggregateService(
                batchRepository, taskRepository, eventRepository
        );
        TaskBatchEntity batch = new TaskBatchEntity();
        batch.setBatchId("batch-cancel");
        batch.setTenantId("tenant-a");
        batch.setParentTaskId("parent-cancel");
        batch.setTotalItems(1);
        batch.setRunningItems(1);
        batch.setStatus("RUNNING");
        TaskEntity parent = task("parent-cancel", "", "RUNNING");
        parent.setBatchId("batch-cancel");
        parent.setBatchIsParent(true);
        when(batchRepository.lockByBatchIdAndTenantId("batch-cancel", "tenant-a")).thenReturn(Optional.of(batch));
        when(taskRepository.findByTenantIdAndBatchIdAndBatchIsParentFalse("tenant-a", "batch-cancel"))
                .thenReturn(List.of(task("child-cancel", "doc-a", "CANCELLED")));
        when(taskRepository.lockByTaskIdAndTenantId("parent-cancel", "tenant-a")).thenReturn(Optional.of(parent));
        when(batchRepository.save(any(TaskBatchEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(batchRepository.saveAndFlush(any(TaskBatchEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(eventRepository.findByTenantIdAndEventKey(any(), any())).thenReturn(Optional.empty());

        TaskBatchEntity result = service.refresh("tenant-a", "batch-cancel");

        assertEquals("CANCELLED", result.getStatus());
        assertEquals(1, result.getCancelledItems());
        assertEquals("CANCELLED", parent.getStatus());
    }

    private TaskEntity task(String id, String docId, String status) {
        TaskEntity task = new TaskEntity();
        task.setTaskId(id);
        task.setTenantId("tenant-a");
        task.setKbId("kb-a");
        task.setDocId(docId);
        task.setTaskType(docId.isBlank() ? "BATCH_DOC_CHUNK" : "DOC_CHUNK");
        task.setStatus(status);
        task.setStage(status);
        task.setBatchId(docId.isBlank() ? "" : "batch-a");
        task.setStateOwner("CONTROL");
        return task;
    }
}
