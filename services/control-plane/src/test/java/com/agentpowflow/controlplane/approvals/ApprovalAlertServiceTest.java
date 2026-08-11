package com.agentpowflow.controlplane.approvals;

import com.agentpowflow.controlplane.runs.RunApprovalEntity;
import com.agentpowflow.controlplane.runs.RunApprovalRepository;
import com.agentpowflow.controlplane.web.dto.ApprovalAlertDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ApprovalAlertServiceTest {
    private ApprovalAlertRepository alertRepository;
    private ApprovalAlertDeliveryRepository deliveryRepository;
    private RunApprovalRepository approvalRepository;
    private ApprovalAlertProperties properties;
    private ApprovalAlertService service;

    @BeforeEach
    void setUp() {
        alertRepository = mock(ApprovalAlertRepository.class);
        deliveryRepository = mock(ApprovalAlertDeliveryRepository.class);
        approvalRepository = mock(RunApprovalRepository.class);
        properties = new ApprovalAlertProperties();
        service = new ApprovalAlertService(
                alertRepository, deliveryRepository, approvalRepository, properties, new ObjectMapper()
        );
        when(deliveryRepository.save(any(ApprovalAlertDeliveryEntity.class))).thenAnswer(invocation -> {
            ApprovalAlertDeliveryEntity item = invocation.getArgument(0);
            item.prePersist();
            return item;
        });
    }

    @Test
    void evaluationCreatesIdempotentDueSoonAndOverdueAlertsWithDisabledDelivery() {
        Instant now = Instant.now();
        RunApprovalEntity dueSoon = approval("approval-due", now.plusSeconds(120));
        RunApprovalEntity overdue = approval("approval-overdue", now.minusSeconds(1));
        when(approvalRepository.findTop100ByStatusAndExpiresAtLessThanEqualOrderByExpiresAtAsc(
                anyString(), any(Instant.class)
        )).thenReturn(List.of(overdue, dueSoon));
        when(alertRepository.insertIgnore(
                anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), anyString(), anyString(), anyString(), any(Instant.class)
        )).thenReturn(1);

        ApprovalAlertDto.EvaluationView result = service.evaluate();

        assertEquals(2, result.examined_count());
        assertEquals(2, result.created_count());
        verify(alertRepository).resolveDueSoonAlert("approval-overdue", result.evaluated_at());
        verify(deliveryRepository, org.mockito.Mockito.times(2)).save(any(ApprovalAlertDeliveryEntity.class));
    }

    @Test
    void duplicateAlertDoesNotCreateAnotherDelivery() {
        RunApprovalEntity dueSoon = approval("approval-due", Instant.now().plusSeconds(120));
        when(approvalRepository.findTop100ByStatusAndExpiresAtLessThanEqualOrderByExpiresAtAsc(
                anyString(), any(Instant.class)
        )).thenReturn(List.of(dueSoon));
        when(alertRepository.insertIgnore(
                anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), anyString(), anyString(), anyString(), any(Instant.class)
        )).thenReturn(0);

        ApprovalAlertDto.EvaluationView result = service.evaluate();

        assertEquals(0, result.created_count());
        verify(deliveryRepository, never()).save(any(ApprovalAlertDeliveryEntity.class));
    }

    @Test
    void timedOutApprovalResolvesQueueRiskAndCreatesCriticalAlert() {
        RunApprovalEntity approval = approval("approval-timeout", Instant.now().minusSeconds(1));
        when(alertRepository.insertIgnore(
                anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                anyString(), anyString(), anyString(), anyString(), any(Instant.class)
        )).thenReturn(1);
        Instant now = Instant.now();

        service.onApprovalTerminal(approval, true, now);

        verify(alertRepository).resolveQueueRiskAlerts("approval-timeout", now);
        verify(alertRepository).insertIgnore(
                anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                org.mockito.ArgumentMatchers.eq("TIMED_OUT"), org.mockito.ArgumentMatchers.eq("CRITICAL"),
                anyString(), anyString(), org.mockito.ArgumentMatchers.eq(now)
        );
    }

    @Test
    void statsSurfaceDeliveryDeadLettersAsCriticalHealth() {
        ApprovalAlertRepository.ApprovalAlertStatsProjection projection = mock(
                ApprovalAlertRepository.ApprovalAlertStatsProjection.class
        );
        when(projection.getOpenCount()).thenReturn(0L);
        when(projection.getCriticalCount()).thenReturn(0L);
        when(alertRepository.summarize(anyString(), any(Instant.class))).thenReturn(projection);
        when(deliveryRepository.countDeadLetters("tenant-a")).thenReturn(2L);

        ApprovalAlertDto.StatsView result = service.stats("tenant-a");

        assertEquals("CRITICAL", result.health());
        assertEquals(2, result.delivery_dead_letter_count());
    }

    @Test
    void acknowledgeIsIdempotentButResolvedAlertFailsClosed() {
        ApprovalAlertEntity alert = alert("OPEN");
        when(alertRepository.lockByAlertId("alert-1")).thenReturn(Optional.of(alert));
        when(alertRepository.saveAndFlush(alert)).thenReturn(alert);
        when(deliveryRepository.findByAlertId("alert-1")).thenReturn(Optional.empty());

        ApprovalAlertDto.AlertView result = service.acknowledge("tenant-a", "admin", "alert-1");
        ApprovalAlertDto.AlertView duplicate = service.acknowledge("tenant-a", "admin", "alert-1");

        assertEquals("ACKNOWLEDGED", result.status());
        assertEquals("ACKNOWLEDGED", duplicate.status());
        alert.setStatus("RESOLVED");
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> service.acknowledge("tenant-a", "admin", "alert-1"));
        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
    }

    @Test
    void replayRequiresConfiguredDestination() {
        ApprovalAlertEntity alert = alert("OPEN");
        ApprovalAlertDeliveryEntity delivery = delivery("DISABLED");
        when(alertRepository.findByAlertIdAndTenantId("alert-1", "tenant-a")).thenReturn(Optional.of(alert));
        when(deliveryRepository.findByAlertId("alert-1")).thenReturn(Optional.of(delivery));

        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> service.replay("tenant-a", "alert-1"));

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        assertTrue(error.getReason().contains("not configured"));
    }

    private RunApprovalEntity approval(String id, Instant expiresAt) {
        RunApprovalEntity item = new RunApprovalEntity();
        item.setApprovalId(id);
        item.setTenantId("tenant-a");
        item.setRunId("run-1");
        item.setNodeId("review");
        item.setNodeType("human.approval");
        item.setTitle("Review");
        item.setAssigneeRolesJson("[\"TENANT_ADMIN\"]");
        item.setCheckpointJson("{}");
        item.setTimeoutAction("REJECT");
        item.setExpiresAt(expiresAt);
        return item;
    }

    private ApprovalAlertEntity alert(String status) {
        ApprovalAlertEntity item = new ApprovalAlertEntity();
        item.setAlertId("alert-1");
        item.setDedupeKey("approval:approval-1:overdue");
        item.setTenantId("tenant-a");
        item.setApprovalId("approval-1");
        item.setRunId("run-1");
        item.setNodeId("review");
        item.setAlertType("OVERDUE");
        item.setSeverity("CRITICAL");
        item.setMessage("Approval is overdue");
        item.setAssigneeRolesJson("[\"TENANT_ADMIN\"]");
        item.setStatus(status);
        item.prePersist();
        return item;
    }

    private ApprovalAlertDeliveryEntity delivery(String status) {
        ApprovalAlertDeliveryEntity item = new ApprovalAlertDeliveryEntity();
        item.setDeliveryId("delivery-1");
        item.setAlertId("alert-1");
        item.setTenantId("tenant-a");
        item.setPayloadJson("{}");
        item.setStatus(status);
        item.setMaxAttempts(3);
        item.prePersist();
        return item;
    }
}
