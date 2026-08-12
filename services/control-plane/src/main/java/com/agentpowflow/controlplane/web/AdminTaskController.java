package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.tasks.TaskAuthorityService;
import com.agentpowflow.controlplane.tasks.DocumentBatchCommandService;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class AdminTaskController {
    private final TaskAuthorityService taskService;
    private final DocumentBatchCommandService batchCommandService;

    public AdminTaskController(TaskAuthorityService taskService, DocumentBatchCommandService batchCommandService) {
        this.taskService = taskService;
        this.batchCommandService = batchCommandService;
    }

    @GetMapping("/admin/tasks")
    public TaskDto.ListResponse list(
            @RequestParam(name = "kb_id", required = false) String kbId,
            @RequestParam(defaultValue = "100") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.ListResponse("ok", taskService.list(claims.tenantId(), kbId, limit));
    }

    @GetMapping("/tasks")
    public TaskDto.ListResponse workspaceList(
            @RequestParam(name = "kb_id", required = false) String kbId,
            @RequestParam(defaultValue = "100") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.ListResponse("ok", taskService.list(claims.tenantId(), kbId, limit));
    }

    @DeleteMapping("/admin/tasks")
    public TaskDto.ClearResponse clear(
            @RequestParam(name = "kb_id", required = false) String kbId,
            Authentication authentication
    ) {
        return taskService.clearFinished(claims(authentication).tenantId(), kbId);
    }

    @DeleteMapping("/tasks")
    public TaskDto.ClearResponse workspaceClear(
            @RequestParam(name = "kb_id", required = false) String kbId,
            Authentication authentication
    ) {
        return taskService.clearFinished(claims(authentication).tenantId(), kbId);
    }

    @PostMapping("/admin/tasks/{taskId}/cancel")
    public TaskDto.CancelResponse cancel(@PathVariable String taskId, Authentication authentication) {
        return taskService.cancel(claims(authentication).tenantId(), taskId);
    }

    @PostMapping("/tasks/{taskId}/cancel")
    public TaskDto.CancelResponse workspaceCancel(@PathVariable String taskId, Authentication authentication) {
        return taskService.cancel(claims(authentication).tenantId(), taskId);
    }

    @GetMapping("/admin/tasks/{taskId}/events")
    public TaskDto.EventListResponse events(
            @PathVariable String taskId,
            @RequestParam(defaultValue = "200") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.EventListResponse(
                "ok", taskId, taskService.events(claims.tenantId(), taskId, limit)
        );
    }

    @GetMapping("/tasks/{taskId}/events")
    public TaskDto.EventListResponse workspaceEvents(
            @PathVariable String taskId,
            @RequestParam(defaultValue = "200") int limit,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new TaskDto.EventListResponse(
                "ok", taskId, taskService.events(claims.tenantId(), taskId, limit)
        );
    }

    @GetMapping("/admin/tasks/batches/{batchId}")
    public TaskDto.TaskBatchDetailResponse batch(
            @PathVariable String batchId,
            Authentication authentication
    ) {
        return batchCommandService.detail(claims(authentication).tenantId(), batchId);
    }

    @GetMapping("/tasks/batches/{batchId}")
    public TaskDto.TaskBatchDetailResponse workspaceBatch(
            @PathVariable String batchId, Authentication authentication
    ) {
        return batchCommandService.detail(claims(authentication).tenantId(), batchId);
    }

    @PostMapping("/admin/tasks/batches/{batchId}/retry-failed")
    public TaskDto.BatchAcceptedResponse retryBatchFailed(
            @PathVariable String batchId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return batchCommandService.retryFailed(
                claims.tenantId(), claims.sub(), java.util.UUID.randomUUID().toString(), batchId
        );
    }

    @PostMapping("/tasks/batches/{batchId}/retry-failed")
    public TaskDto.BatchAcceptedResponse workspaceRetryBatchFailed(
            @PathVariable String batchId, Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return batchCommandService.retryFailed(
                claims.tenantId(), claims.sub(), java.util.UUID.randomUUID().toString(), batchId
        );
    }

    @GetMapping("/tasks/{taskId}")
    public TaskDto.StatusResponse status(@PathVariable String taskId, Authentication authentication) {
        return taskService.status(claims(authentication).tenantId(), taskId);
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
