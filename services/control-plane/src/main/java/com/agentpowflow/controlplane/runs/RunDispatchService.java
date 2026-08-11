package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.net.InetAddress;
import java.util.Set;
import java.util.UUID;

@Service
public class RunDispatchService {
    private static final Logger log = LoggerFactory.getLogger(RunDispatchService.class);

    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private final RunService runService;
    private final RunDispatchOutboxService outboxService;
    private final ExecutionDispatchProperties properties;
    private final InternalApiProperties internalApiProperties;
    private final RestClient restClient = RestClient.create();
    private final String dispatcherId = dispatcherId();

    public RunDispatchService(
            RunService runService,
            RunDispatchOutboxService outboxService,
            ExecutionDispatchProperties properties,
            InternalApiProperties internalApiProperties
    ) {
        this.runService = runService;
        this.outboxService = outboxService;
        this.properties = properties;
        this.internalApiProperties = internalApiProperties;
    }

    @Scheduled(fixedDelayString = "${control-plane.execution-dispatch.interval-ms:1000}")
    public void dispatchOutbox() {
        if (!properties.isEnabled()) return;
        for (RunDispatchOutboxEntity item : outboxService.claimBatch(dispatcherId)) {
            String runId = item.getRunId();
            try {
                RunDto.ExecutionContextView context = runService.internalExecutionContext(runId);
                if (TERMINAL.contains(context.status())) {
                    outboxService.cancelClaim(item.getOutboxId(), dispatcherId);
                    continue;
                }
                if ("WAITING_APPROVAL".equals(context.status())) {
                    outboxService.cancelClaim(item.getOutboxId(), dispatcherId);
                    continue;
                }
                restClient.post()
                        .uri(properties.getUrl())
                        .header("X-Internal-Token", internalApiProperties.getToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(context)
                        .retrieve()
                        .toBodilessEntity();
                outboxService.markPublished(item.getOutboxId(), dispatcherId);
            } catch (RuntimeException ex) {
                outboxService.markFailure(item.getOutboxId(), dispatcherId, ex);
                log.warn("Run dispatch failed runId={} reason={}", runId, ex.getMessage());
            }
        }
    }

    private static String dispatcherId() {
        try {
            return InetAddress.getLocalHost().getHostName() + ":" + UUID.randomUUID();
        } catch (Exception ignored) {
            return "control-plane:" + UUID.randomUUID();
        }
    }
}
