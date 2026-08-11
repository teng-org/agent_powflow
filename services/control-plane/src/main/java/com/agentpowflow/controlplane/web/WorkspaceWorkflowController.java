package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.canvas.CanvasModels;
import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.web.dto.CanvasValidationDto;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@RestController
@RequestMapping("/api/v1/workflows")
public class WorkspaceWorkflowController {
    private final WorkflowService workflowService;
    private final ProductAssetService productAssetService;

    public WorkspaceWorkflowController(WorkflowService workflowService, ProductAssetService productAssetService) {
        this.workflowService = workflowService;
        this.productAssetService = productAssetService;
    }

    @GetMapping
    public WorkflowDto.WorkflowListResponse list(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        List<WorkflowDto.WorkflowView> owned = workflowService.list(claims.tenantId()).stream()
                .filter(item -> claims.sub().equals(item.created_by()))
                .filter(item -> !"ARCHIVED".equals(item.status()))
                .toList();
        return new WorkflowDto.WorkflowListResponse("ok", owned);
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
        return response(item);
    }

    @GetMapping("/{workflowId}")
    public WorkflowDto.WorkflowResponse get(@PathVariable String workflowId, Authentication authentication) {
        return response(owned(authentication, workflowId));
    }

    @PutMapping("/{workflowId}/draft")
    public WorkflowDto.WorkflowResponse saveDraft(
            @PathVariable String workflowId,
            @Valid @RequestBody WorkflowDto.SaveDraftRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        owned(authentication, workflowId);
        WorkflowDto.WorkflowView item = workflowService.saveDraft(
                claims.tenantId(), claims.sub(), workflowId, request.name(), request.description(), request.dsl(),
                request.layout(), request.expected_revision()
        );
        return response(item);
    }

    @PostMapping("/{workflowId}/validate")
    public CanvasValidationDto.ValidateResponse validate(
            @PathVariable String workflowId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        owned(authentication, workflowId);
        return validation(workflowService.validate(claims.tenantId(), workflowId));
    }

    @PostMapping("/{workflowId}/publish")
    public WorkflowDto.VersionResponse publish(
            @PathVariable String workflowId,
            @RequestBody(required = false) WorkflowDto.PublishRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        owned(authentication, workflowId);
        return new WorkflowDto.VersionResponse(
                "ok", workflowService.publish(
                        claims.tenantId(), claims.sub(), workflowId,
                        request == null ? null : request.expected_revision()
                )
        );
    }

    @GetMapping("/{workflowId}/versions")
    public WorkflowDto.VersionListResponse versions(
            @PathVariable String workflowId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        owned(authentication, workflowId);
        return new WorkflowDto.VersionListResponse("ok", workflowService.listVersions(claims.tenantId(), workflowId));
    }

    @DeleteMapping("/{workflowId}")
    public ProductAssetDto.DeleteResponse delete(
            @PathVariable String workflowId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return productAssetService.deleteWorkflow(claims.tenantId(), claims.sub(), workflowId);
    }

    private WorkflowDto.WorkflowView owned(Authentication authentication, String workflowId) {
        TokenClaims claims = claims(authentication);
        WorkflowDto.WorkflowView item = workflowService.get(claims.tenantId(), workflowId);
        if (!claims.sub().equals(item.created_by())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workflow is not owned by current user");
        }
        return item;
    }

    private WorkflowDto.WorkflowResponse response(WorkflowDto.WorkflowView item) {
        return new WorkflowDto.WorkflowResponse("ok", item, validation(workflowService.validateJson(item.draft_dsl())));
    }

    private CanvasValidationDto.ValidateResponse validation(CanvasModels.ValidationResult result) {
        return new CanvasValidationDto.ValidateResponse(
                result.valid(),
                result.errors().stream().map(this::issue).toList(),
                result.warnings().stream().map(this::issue).toList()
        );
    }

    private CanvasValidationDto.IssueDto issue(CanvasModels.ValidationIssue issue) {
        return new CanvasValidationDto.IssueDto(
                issue.nodeId(), issue.edgeId(), issue.code(), issue.message(), issue.suggestion()
        );
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
