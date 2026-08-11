package com.agentpowflow.controlplane.runs;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class RunDispatchOutboxBackfill {
    private static final Logger log = LoggerFactory.getLogger(RunDispatchOutboxBackfill.class);
    private final RunRepository runRepository;
    private final RunDispatchOutboxRepository outboxRepository;
    private final RunDispatchOutboxService outboxService;

    public RunDispatchOutboxBackfill(
            RunRepository runRepository,
            RunDispatchOutboxRepository outboxRepository,
            RunDispatchOutboxService outboxService
    ) {
        this.runRepository = runRepository;
        this.outboxRepository = outboxRepository;
        this.outboxService = outboxService;
    }

    @EventListener(ApplicationReadyEvent.class)
    @Transactional
    public void backfillPendingRuns() {
        int created = 0;
        for (RunEntity run : runRepository.findTop1000ByStatusOrderByCreatedAtAsc("PENDING")) {
            if (outboxRepository.findByRunId(run.getRunId()).isEmpty()) {
                outboxService.enqueue(run.getTenantId(), run.getRunId());
                created++;
            }
        }
        if (created > 0) log.info("Backfilled {} pending Run dispatch outbox records", created);
    }
}
