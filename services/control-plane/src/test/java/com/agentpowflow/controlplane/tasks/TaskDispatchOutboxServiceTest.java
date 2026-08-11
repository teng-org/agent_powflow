package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TaskDispatchOutboxServiceTest {
    private TaskDispatchOutboxRepository repository;
    private TaskRepository taskRepository;
    private TaskEventRepository eventRepository;
    private TaskDispatchOutboxService service;

    @BeforeEach
    void setUp() {
        repository = mock(TaskDispatchOutboxRepository.class);
        taskRepository = mock(TaskRepository.class);
        eventRepository = mock(TaskEventRepository.class);
        TaskDispatchProperties properties = new TaskDispatchProperties();
        properties.setMaxAttempts(3);
        properties.setRetryBaseSeconds(2);
        properties.setRetryMaxSeconds(10);
        service = new TaskDispatchOutboxService(
                repository, taskRepository, eventRepository, properties,
                mock(TaskBatchAggregateService.class)
        );
        when(repository.save(any(TaskDispatchOutboxEntity.class))).thenAnswer(invocation -> persist(invocation.getArgument(0)));
        when(repository.saveAndFlush(any(TaskDispatchOutboxEntity.class))).thenAnswer(invocation -> persist(invocation.getArgument(0)));
        when(repository.saveAllAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void enqueueCreatesDurableCommandEnvelope() {
        TaskDispatchOutboxEntity item = service.enqueue("tenant-a", "task-1", "DOC_INGEST_TEXT", "{\"content\":\"hello\"}");

        assertNotNull(item.getOutboxId());
        assertEquals("PENDING", item.getStatus());
        assertEquals(3, item.getMaxAttempts());
        assertEquals("DOC_INGEST_TEXT", item.getCommandType());
    }

    @Test
    void claimAssignsOwnershipAndIncrementsAttempt() {
        TaskDispatchOutboxEntity item = item("PENDING", 0, "");
        when(repository.lockDispatchable(any(), anyInt())).thenReturn(List.of(item));

        service.claimBatch("dispatcher-a");

        assertEquals("IN_FLIGHT", item.getStatus());
        assertEquals(1, item.getAttemptCount());
        assertEquals("dispatcher-a", item.getLockedBy());
        assertNotNull(item.getLockedUntil());
    }

    @Test
    void exhaustedFailureDeadLettersAndFinalizesControlTask() {
        TaskDispatchOutboxEntity item = item("IN_FLIGHT", 3, "dispatcher-a");
        TaskEntity task = task("PENDING", "QUEUED");
        when(repository.lockByOutboxId(item.getOutboxId())).thenReturn(Optional.of(item));
        when(taskRepository.lockByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        service.markFailure(item.getOutboxId(), "dispatcher-a", new RuntimeException("broker unavailable"));

        assertEquals("DEAD_LETTER", item.getStatus());
        assertNotNull(item.getDeadLetteredAt());
        assertEquals("FAILED", task.getStatus());
        assertEquals("DISPATCH_DEAD_LETTER", task.getStage());
        verify(eventRepository).save(any(TaskEventEntity.class));
    }

    @Test
    void manualReplayResetsDeadLetterAndTaskState() {
        TaskDispatchOutboxEntity item = item("DEAD_LETTER", 3, "");
        item.setDeadLetteredAt(Instant.now());
        TaskEntity task = task("FAILED", "DISPATCH_DEAD_LETTER");
        when(repository.findByOutboxIdAndTenantId(item.getOutboxId(), "tenant-a")).thenReturn(Optional.of(item));
        when(repository.lockByOutboxId(item.getOutboxId())).thenReturn(Optional.of(item));
        when(taskRepository.lockByTaskIdAndTenantId("task-1", "tenant-a")).thenReturn(Optional.of(task));

        TaskDto.DispatchOutboxView replayed = service.replay("tenant-a", item.getOutboxId());

        assertEquals("PENDING", replayed.status());
        assertEquals(0, replayed.attempt_count());
        assertEquals("PENDING", task.getStatus());
        assertEquals("QUEUED", task.getStage());
        assertEquals("CONTROL", task.getStateOwner());
        verify(eventRepository).save(any(TaskEventEntity.class));
    }

    private TaskDispatchOutboxEntity item(String status, int attempts, String lockedBy) {
        TaskDispatchOutboxEntity item = new TaskDispatchOutboxEntity();
        item.setTenantId("tenant-a");
        item.setTaskId("task-1");
        item.setCommandType("DOC_INGEST_TEXT");
        item.setPayloadJson("{}");
        item.setStatus(status);
        item.setAttemptCount(attempts);
        item.setMaxAttempts(3);
        item.setLockedBy(lockedBy);
        item.prePersist();
        return item;
    }

    private TaskDispatchOutboxEntity persist(TaskDispatchOutboxEntity item) {
        if (item.getOutboxId() == null) item.prePersist();
        return item;
    }

    private TaskEntity task(String status, String stage) {
        TaskEntity task = new TaskEntity();
        task.setTaskId("task-1");
        task.setTenantId("tenant-a");
        task.setKbId("kb-a");
        task.setDocId("doc-a");
        task.setTaskType("DOC_INGEST");
        task.setFilename("sample.txt");
        task.setStatus(status);
        task.setStage(stage);
        task.setStateOwner("CONTROL");
        return task;
    }
}
