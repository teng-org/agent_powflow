package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.runs.InternalApiProperties;
import com.agentpowflow.controlplane.tasks.TaskAuthorityService;
import com.agentpowflow.controlplane.web.dto.TaskDto;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/api/v1/internal/tasks")
public class InternalTaskEventController {
    private final TaskAuthorityService taskService;
    private final InternalApiProperties properties;

    public InternalTaskEventController(TaskAuthorityService taskService, InternalApiProperties properties) {
        this.taskService = taskService;
        this.properties = properties;
    }

    @PostMapping("/{taskId}/events")
    public TaskDto.InternalEventResponse append(
            @PathVariable String taskId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @Valid @RequestBody TaskDto.InternalTaskEventRequest request
    ) {
        requireInternalToken(token);
        TaskAuthorityService.InternalEventResult result = taskService.appendInternalEvent(taskId, request);
        return new TaskDto.InternalEventResponse("ok", result.task(), result.event(), result.duplicate());
    }

    private void requireInternalToken(String supplied) {
        String expected = properties.getToken();
        if (supplied == null || expected == null || expected.isBlank() || !MessageDigest.isEqual(
                supplied.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8)
        )) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid internal token");
        }
    }
}
