package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/v1/admin/product-assets")
public class AdminProductAssetController {
    private final ProductAssetService service;

    public AdminProductAssetController(ProductAssetService service) {
        this.service = service;
    }

    @GetMapping("/agents")
    public ProductAssetDto.AgentListResponse agents(Authentication authentication) {
        requirePlatformAdmin(authentication);
        return new ProductAssetDto.AgentListResponse("ok", service.listPlatformAgents());
    }

    @GetMapping("/deployments")
    public ProductAssetDto.DeploymentListResponse deployments(Authentication authentication) {
        requirePlatformAdmin(authentication);
        return new ProductAssetDto.DeploymentListResponse("ok", service.listPlatformDeployments());
    }

    @PatchMapping("/deployments/{deploymentId}/status")
    public ProductAssetDto.DeploymentResponse deploymentStatus(
            @PathVariable String deploymentId,
            @RequestBody ProductAssetDto.DeploymentStatusRequest request,
            Authentication authentication
    ) {
        requirePlatformAdmin(authentication);
        return new ProductAssetDto.DeploymentResponse(
                "ok", service.setPlatformDeploymentStatus(deploymentId, request.status())
        );
    }

    private void requirePlatformAdmin(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)
                || !claims.roles().contains("PLATFORM_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "platform administrator role required");
        }
    }
}
