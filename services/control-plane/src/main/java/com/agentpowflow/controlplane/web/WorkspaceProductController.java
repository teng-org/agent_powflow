package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class WorkspaceProductController {
    private final ProductAssetService productAssetService;

    public WorkspaceProductController(ProductAssetService productAssetService) {
        this.productAssetService = productAssetService;
    }

    @GetMapping("/agents")
    public ProductAssetDto.AgentListResponse agents(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentListResponse(
                "ok", productAssetService.listAgents(claims.tenantId(), claims.sub())
        );
    }

    @PostMapping("/agents")
    public ProductAssetDto.AgentResponse createAgent(
            @Valid @RequestBody ProductAssetDto.AgentUpsertRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentResponse(
                "ok", productAssetService.createAgent(claims.tenantId(), claims.sub(), request)
        );
    }

    @GetMapping("/agents/{agentId}")
    public ProductAssetDto.AgentResponse agent(@PathVariable String agentId, Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentResponse(
                "ok", productAssetService.getAgent(claims.tenantId(), claims.sub(), agentId)
        );
    }

    @PutMapping("/agents/{agentId}")
    public ProductAssetDto.AgentResponse updateAgent(
            @PathVariable String agentId,
            @Valid @RequestBody ProductAssetDto.AgentUpsertRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentResponse(
                "ok", productAssetService.updateAgent(claims.tenantId(), claims.sub(), agentId, request)
        );
    }

    @PostMapping("/agents/{agentId}/publish")
    public ProductAssetDto.AgentVersionResponse publishAgent(
            @PathVariable String agentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentVersionResponse(
                "ok", productAssetService.publishAgent(claims.tenantId(), claims.sub(), agentId)
        );
    }

    @GetMapping("/agents/{agentId}/versions")
    public ProductAssetDto.AgentVersionListResponse agentVersions(
            @PathVariable String agentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.AgentVersionListResponse(
                "ok", productAssetService.listAgentVersions(claims.tenantId(), claims.sub(), agentId)
        );
    }

    @DeleteMapping("/agents/{agentId}")
    public ProductAssetDto.DeleteResponse deleteAgent(
            @PathVariable String agentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return productAssetService.deleteAgent(claims.tenantId(), claims.sub(), agentId);
    }

    @GetMapping("/deployments")
    public ProductAssetDto.DeploymentListResponse deployments(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.DeploymentListResponse(
                "ok", productAssetService.listDeployments(claims.tenantId(), claims.sub())
        );
    }

    @PostMapping("/deployments")
    public ProductAssetDto.DeploymentResponse createDeployment(
            @Valid @RequestBody ProductAssetDto.DeploymentCreateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.DeploymentResponse(
                "ok", productAssetService.createDeployment(claims.tenantId(), claims.sub(), request)
        );
    }

    @PatchMapping("/deployments/{deploymentId}/status")
    public ProductAssetDto.DeploymentResponse deploymentStatus(
            @PathVariable String deploymentId,
            @Valid @RequestBody ProductAssetDto.DeploymentStatusRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.DeploymentResponse(
                "ok", productAssetService.setDeploymentStatus(
                        claims.tenantId(), claims.sub(), deploymentId, request.status()
                )
        );
    }

    @DeleteMapping("/deployments/{deploymentId}")
    public ProductAssetDto.DeleteResponse deleteDeployment(
            @PathVariable String deploymentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return productAssetService.deleteDeployment(claims.tenantId(), claims.sub(), deploymentId);
    }

    @GetMapping("/deployments/{deploymentId}/api-keys")
    public ProductAssetDto.ApiKeyListResponse apiKeys(
            @PathVariable String deploymentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.ApiKeyListResponse(
                "ok", productAssetService.listApiKeys(claims.tenantId(), claims.sub(), deploymentId)
        );
    }

    @PostMapping("/deployments/{deploymentId}/api-keys")
    public ProductAssetDto.ApiKeyCreatedResponse createApiKey(
            @PathVariable String deploymentId,
            @Valid @RequestBody ProductAssetDto.ApiKeyCreateRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        ProductAssetService.IssuedApiKey issued = productAssetService.createApiKey(
                claims.tenantId(), claims.sub(), deploymentId, request
        );
        return new ProductAssetDto.ApiKeyCreatedResponse("ok", issued.item(), issued.secret());
    }

    @PostMapping("/deployment-api-keys/{apiKeyId}/revoke")
    public ProductAssetDto.ApiKeyCreatedResponse revokeApiKey(
            @PathVariable String apiKeyId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.ApiKeyCreatedResponse(
                "ok", productAssetService.revokeApiKey(claims.tenantId(), claims.sub(), apiKeyId), null
        );
    }

    @GetMapping("/deployments/{deploymentId}/invocations")
    public ProductAssetDto.InvocationListResponse invocations(
            @PathVariable String deploymentId,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        return new ProductAssetDto.InvocationListResponse(
                "ok", productAssetService.listInvocations(claims.tenantId(), claims.sub(), deploymentId)
        );
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new IllegalStateException("authenticated token claims missing");
        }
        return claims;
    }
}
