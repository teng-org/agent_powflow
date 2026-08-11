package com.agentpowflow.controlplane.tasks;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class DocumentUploadRecoveryService {
    private final DocumentUploadIntentService intentService;
    private final KnowledgeExecutionClient executionClient;

    public DocumentUploadRecoveryService(
            DocumentUploadIntentService intentService,
            KnowledgeExecutionClient executionClient
    ) {
        this.intentService = intentService;
        this.executionClient = executionClient;
    }

    @Scheduled(fixedDelayString = "${control-plane.task-dispatch.upload-recovery-interval-ms:60000}")
    public void expireAbandonedUploads() {
        java.util.Map<String, DocumentUploadIntentService.CleanupTarget> targets = new java.util.LinkedHashMap<>();
        for (DocumentUploadIntentService.CleanupTarget target : intentService.expireStale()) {
            targets.put(target.taskId(), target);
        }
        for (DocumentUploadIntentService.CleanupTarget target : intentService.pendingCleanup()) {
            targets.put(target.taskId(), target);
        }
        for (DocumentUploadIntentService.CleanupTarget target : targets.values()) {
            if (executionClient.cleanupStagedDocument(
                    target.tenantId(), target.kbId(), target.docId(), target.taskId()
            )) {
                intentService.markCleanupComplete(target.tenantId(), target.taskId());
            }
        }
    }
}
