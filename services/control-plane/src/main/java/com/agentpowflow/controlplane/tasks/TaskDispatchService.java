package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.net.InetAddress;
import java.util.UUID;

@Service
public class TaskDispatchService {
    private static final Logger log = LoggerFactory.getLogger(TaskDispatchService.class);
    private final TaskDispatchOutboxService outboxService;
    private final TaskDispatchProperties properties;
    private final KnowledgeExecutionClient executionClient;
    private final String dispatcherId = dispatcherId();

    public TaskDispatchService(
            TaskDispatchOutboxService outboxService,
            TaskDispatchProperties properties,
            KnowledgeExecutionClient executionClient
    ) {
        this.outboxService = outboxService;
        this.properties = properties;
        this.executionClient = executionClient;
    }

    @Scheduled(fixedDelayString = "${control-plane.task-dispatch.interval-ms:1000}")
    public void dispatchOutbox() {
        if (!properties.isEnabled()) return;
        for (TaskDispatchOutboxEntity item : outboxService.claimBatch(dispatcherId)) {
            try {
                executionClient.dispatchTask(item.getPayloadJson());
                outboxService.markPublished(item.getOutboxId(), dispatcherId);
            } catch (RuntimeException exception) {
                outboxService.markFailure(item.getOutboxId(), dispatcherId, exception);
                log.warn("Task dispatch failed taskId={} reason={}", item.getTaskId(), exception.getMessage());
            }
        }
    }

    private static String dispatcherId() {
        try { return InetAddress.getLocalHost().getHostName() + ":task:" + UUID.randomUUID(); }
        catch (Exception ignored) { return "control-task:" + UUID.randomUUID(); }
    }
}
