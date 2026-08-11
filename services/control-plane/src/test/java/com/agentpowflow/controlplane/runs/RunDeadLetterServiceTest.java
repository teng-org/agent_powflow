package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import java.util.Optional;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RunDeadLetterServiceTest {
    private RunDeadLetterRepository repository;
    private RunDeadLetterAuditRepository auditRepository;
    private RunRepository runRepository;
    private RunDispatchOutboxService outboxService;
    private RunDeadLetterService service;

    @BeforeEach void setUp() {
        repository=mock(RunDeadLetterRepository.class); auditRepository=mock(RunDeadLetterAuditRepository.class);
        runRepository=mock(RunRepository.class); outboxService=mock(RunDispatchOutboxService.class);
        service=new RunDeadLetterService(repository,auditRepository,runRepository,outboxService,new ObjectMapper());
        when(repository.saveAndFlush(any())).thenAnswer(i->{RunDeadLetterEntity d=i.getArgument(0);d.prePersist();return d;});
        when(repository.save(any())).thenAnswer(i->i.getArgument(0));
        when(auditRepository.save(any())).thenAnswer(i->{RunDeadLetterAuditEntity a=i.getArgument(0);a.prePersist();return a;});
    }

    @Test void ingestsOnceAndWritesAudit() throws Exception {
        when(repository.findByFailedTaskId("task-1")).thenReturn(Optional.empty());
        when(runRepository.findByRunIdAndTenantId("run-1","tenant-a")).thenReturn(Optional.of(new RunEntity()));
        var result=service.ingest(new RunDto.DeadLetterIngestRequest("tenant-a","run-1","task-1","control_run_queue","boom",new ObjectMapper().readTree("{\"x\":1}")));
        assertFalse(result.duplicate()); assertEquals("OPEN",result.item().status()); assertEquals("boom",result.item().error());
        verify(auditRepository).save(any(RunDeadLetterAuditEntity.class));
    }

    @Test void replayRequeuesOutboxAndRecordsActor() {
        RunDeadLetterEntity item=new RunDeadLetterEntity(); item.setTenantId("tenant-a");item.setRunId("run-1");item.setFailedTaskId("task-1");item.setSourceQueue("control_run_queue");item.prePersist();
        when(repository.lockById(item.getDeadLetterId())).thenReturn(Optional.of(item));
        var view=service.replay("tenant-a",item.getDeadLetterId(),"admin-1");
        assertEquals("REPLAYING",view.status()); assertEquals(1,view.replay_count()); assertEquals("admin-1",view.replayed_by());
        verify(outboxService).requeueExecutionDeadLetter("tenant-a","run-1");
        verify(auditRepository).save(any(RunDeadLetterAuditEntity.class));
    }

    @Test void dismissesOpenItemWithAudit() {
        RunDeadLetterEntity item=new RunDeadLetterEntity(); item.setTenantId("tenant-a");item.setRunId("run-1");item.setFailedTaskId("task-1");item.setSourceQueue("control_run_queue");item.prePersist();
        when(repository.lockById(item.getDeadLetterId())).thenReturn(Optional.of(item));
        var view=service.dismiss("tenant-a",item.getDeadLetterId(),"admin-1");
        assertEquals("DISMISSED",view.status()); assertEquals("admin-1",view.replayed_by());
        verifyNoInteractions(outboxService); verify(auditRepository).save(any(RunDeadLetterAuditEntity.class));
    }
}
