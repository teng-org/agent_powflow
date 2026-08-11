package com.agentpowflow.controlplane.tasks;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
public class TaskBatchAggregateService {
    private static final Set<String> ACTIVE = Set.of("PENDING", "RUNNING", "CANCELLING");

    private final TaskBatchRepository batchRepository;
    private final TaskRepository taskRepository;
    private final TaskEventRepository eventRepository;

    public TaskBatchAggregateService(
            TaskBatchRepository batchRepository,
            TaskRepository taskRepository,
            TaskEventRepository eventRepository
    ) {
        this.batchRepository = batchRepository;
        this.taskRepository = taskRepository;
        this.eventRepository = eventRepository;
    }

    @Transactional
    public TaskBatchEntity refreshForTask(TaskEntity task) {
        if (task == null || task.isBatchIsParent() || task.getBatchId() == null || task.getBatchId().isBlank()) {
            return null;
        }
        return refresh(task.getTenantId(), task.getBatchId());
    }

    @Transactional
    public TaskBatchEntity refresh(String tenantId, String batchId) {
        TaskBatchEntity batch = batchRepository.lockByBatchIdAndTenantId(batchId, tenantId).orElse(null);
        if (batch == null) return null;
        List<TaskEntity> children = taskRepository.findByTenantIdAndBatchIdAndBatchIsParentFalse(tenantId, batchId);
        int total = children.size();
        int succeeded = count(children, "SUCCEEDED");
        int failed = count(children, "FAILED");
        int cancelled = count(children, "CANCELLED");
        int running = (int) children.stream().filter(task -> ACTIVE.contains(task.getStatus())).count();
        String status;
        String stage;
        if (running > 0) {
            status = "RUNNING";
            stage = "BATCH_RUNNING";
        } else if (failed > 0) {
            status = "FAILED";
            stage = "BATCH_FAILED";
        } else if (cancelled > 0) {
            status = "CANCELLED";
            stage = "BATCH_CANCELLED";
        } else {
            status = "SUCCEEDED";
            stage = "BATCH_COMPLETED";
        }
        boolean changed = total != batch.getTotalItems()
                || succeeded != batch.getSucceededItems()
                || failed != batch.getFailedItems()
                || cancelled != batch.getCancelledItems()
                || running != batch.getRunningItems()
                || !status.equals(batch.getStatus());
        if (!changed) return batch;

        batch.setTotalItems(total);
        batch.setSucceededItems(succeeded);
        batch.setFailedItems(failed);
        batch.setCancelledItems(cancelled);
        batch.setRunningItems(running);
        batch.setStatus(status);
        batchRepository.save(batch);

        TaskEntity parent = taskRepository.lockByTaskIdAndTenantId(batch.getParentTaskId(), tenantId).orElse(null);
        if (parent != null) {
            int completed = succeeded + failed + cancelled;
            parent.setStatus(status);
            parent.setStage(stage);
            parent.setProgressCurrent(completed);
            parent.setProgressTotal(total);
            parent.setProgressPct(total == 0 ? 0 : Math.min(100, completed * 100 / total));
            parent.setErrorMessage(errorSummary(failed, cancelled));
            parent.setStateOwner("CONTROL");
            taskRepository.save(parent);
            appendAggregateEvent(parent, succeeded, failed, cancelled, running);
        }
        return batchRepository.saveAndFlush(batch);
    }

    private int count(List<TaskEntity> tasks, String status) {
        return (int) tasks.stream().filter(task -> status.equals(task.getStatus())).count();
    }

    private String errorSummary(int failed, int cancelled) {
        if (failed == 0 && cancelled == 0) return null;
        if (failed > 0 && cancelled > 0) return failed + " 项失败，" + cancelled + " 项取消";
        return failed > 0 ? failed + " 项失败" : cancelled + " 项取消";
    }

    private void appendAggregateEvent(
            TaskEntity parent, int succeeded, int failed, int cancelled, int running
    ) {
        String eventKey = "batch:" + parent.getBatchId() + ":aggregate:"
                + succeeded + ":" + failed + ":" + cancelled + ":" + running;
        if (eventRepository.findByTenantIdAndEventKey(parent.getTenantId(), eventKey).isPresent()) return;
        TaskEventEntity event = new TaskEventEntity();
        event.setTaskId(parent.getTaskId());
        event.setTenantId(parent.getTenantId());
        event.setKbId(parent.getKbId());
        event.setDocId("");
        event.setEventKey(eventKey);
        event.setEventType(switch (parent.getStatus()) {
            case "SUCCEEDED" -> "TASK_COMPLETED";
            case "FAILED" -> "TASK_FAILED";
            case "CANCELLED" -> "TASK_CANCELLED";
            default -> "TASK_PROGRESS_STATE";
        });
        event.setStage(parent.getStage());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("authority", "CONTROL");
        payload.put("batch_id", parent.getBatchId());
        payload.put("succeeded_items", succeeded);
        payload.put("failed_items", failed);
        payload.put("cancelled_items", cancelled);
        payload.put("running_items", running);
        event.setPayload(payload);
        eventRepository.save(event);
    }
}
