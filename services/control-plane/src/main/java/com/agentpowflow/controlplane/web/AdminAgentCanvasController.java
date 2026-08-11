package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.canvas.CanvasModels;
import com.agentpowflow.controlplane.canvas.CanvasDslMapper;
import com.agentpowflow.controlplane.canvas.DslValidator;
import com.agentpowflow.controlplane.web.dto.CanvasValidationDto;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/admin/agent-canvas")
public class AdminAgentCanvasController {
    private final DslValidator dslValidator;
    private final CanvasDslMapper canvasDslMapper;

    public AdminAgentCanvasController(DslValidator dslValidator, CanvasDslMapper canvasDslMapper) {
        this.dslValidator = dslValidator;
        this.canvasDslMapper = canvasDslMapper;
    }

    @PostMapping("/validate")
    public CanvasValidationDto.ValidateResponse validate(@Valid @RequestBody CanvasValidationDto.ValidateRequest request) {
        CanvasModels.AgentCanvasDsl dsl = canvasDslMapper.fromRequest(request);

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);
        return new CanvasValidationDto.ValidateResponse(
                result.valid(),
                mapIssues(result.errors()),
                mapIssues(result.warnings())
        );
    }

    private List<CanvasValidationDto.IssueDto> mapIssues(List<CanvasModels.ValidationIssue> issues) {
        return issues.stream()
                .map(issue -> new CanvasValidationDto.IssueDto(
                        issue.nodeId(),
                        issue.edgeId(),
                        issue.code(),
                        issue.message(),
                        issue.suggestion()
                ))
                .toList();
    }
}
