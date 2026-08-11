package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.canvas.CanvasModels;
import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.web.dto.CanvasValidationDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/admin/workflows")
public class AdminWorkflowController {
    private final WorkflowService workflowService;

    public AdminWorkflowController(WorkflowService workflowService) {
        this.workflowService = workflowService;
    }

    @GetMapping
    public WorkflowDto.WorkflowListResponse list(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new WorkflowDto.WorkflowListResponse("ok", workflowService.list(claims.tenantId()));
    }

    @PostMapping
    public WorkflowDto.WorkflowResponse create(
            @Valid @RequestBody WorkflowDto.CreateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        WorkflowDto.WorkflowView item = workflowService.create(
                claims.tenantId(), claims.sub(), request.name(), request.description(), request.dsl(), request.layout()
        );
        return new WorkflowDto.WorkflowResponse("ok", item, validation(item.draft_dsl()));
    }

    @GetMapping("/{workflowId}")
    public WorkflowDto.WorkflowResponse get(@PathVariable String workflowId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        WorkflowDto.WorkflowView item = workflowService.get(claims.tenantId(), workflowId);
        return new WorkflowDto.WorkflowResponse("ok", item, validation(item.draft_dsl()));
    }

    @PutMapping("/{workflowId}/draft")
    public WorkflowDto.WorkflowResponse saveDraft(
            @PathVariable String workflowId,
            @Valid @RequestBody WorkflowDto.SaveDraftRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        WorkflowDto.WorkflowView item = workflowService.saveDraft(
                claims.tenantId(), claims.sub(), workflowId, request.name(), request.description(), request.dsl(),
                request.layout(), request.expected_revision()
        );
        return new WorkflowDto.WorkflowResponse("ok", item, validation(item.draft_dsl()));
    }

    @PostMapping("/{workflowId}/validate")
    public CanvasValidationDto.ValidateResponse validate(@PathVariable String workflowId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return mapValidation(workflowService.validate(claims.tenantId(), workflowId));
    }

    @PostMapping("/{workflowId}/publish")
    public WorkflowDto.VersionResponse publish(
            @PathVariable String workflowId,
            @RequestBody(required = false) WorkflowDto.PublishRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        Long revision = request == null ? null : request.expected_revision();
        return new WorkflowDto.VersionResponse("ok", workflowService.publish(claims.tenantId(), claims.sub(), workflowId, revision));
    }

    @PostMapping("/{workflowId}/archive")
    public WorkflowDto.WorkflowResponse archive(
            @PathVariable String workflowId,
            @RequestBody(required = false) WorkflowDto.PublishRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        Long revision = request == null ? null : request.expected_revision();
        WorkflowDto.WorkflowView item = workflowService.archive(claims.tenantId(), claims.sub(), workflowId, revision);
        return new WorkflowDto.WorkflowResponse("ok", item, validation(item.draft_dsl()));
    }

    @GetMapping("/{workflowId}/versions")
    public WorkflowDto.VersionListResponse versions(@PathVariable String workflowId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new WorkflowDto.VersionListResponse("ok", workflowService.listVersions(claims.tenantId(), workflowId));
    }

    @GetMapping("/{workflowId}/versions/{versionNo}")
    public WorkflowDto.VersionResponse version(
            @PathVariable String workflowId,
            @PathVariable int versionNo,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new WorkflowDto.VersionResponse("ok", workflowService.getVersion(claims.tenantId(), workflowId, versionNo));
    }

    @DeleteMapping("/{workflowId}")
    public WorkflowDto.DeleteResponse delete(@PathVariable String workflowId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        workflowService.deleteArchived(claims.tenantId(), workflowId);
        return new WorkflowDto.DeleteResponse("ok", workflowId);
    }

    private CanvasValidationDto.ValidateResponse validation(com.fasterxml.jackson.databind.JsonNode dsl) {
        return mapValidation(workflowService.validateJson(dsl));
    }

    private CanvasValidationDto.ValidateResponse mapValidation(CanvasModels.ValidationResult result) {
        return new CanvasValidationDto.ValidateResponse(result.valid(), mapIssues(result.errors()), mapIssues(result.warnings()));
    }

    private List<CanvasValidationDto.IssueDto> mapIssues(List<CanvasModels.ValidationIssue> issues) {
        return issues.stream().map(issue -> new CanvasValidationDto.IssueDto(
                issue.nodeId(), issue.edgeId(), issue.code(), issue.message(), issue.suggestion()
        )).toList();
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
