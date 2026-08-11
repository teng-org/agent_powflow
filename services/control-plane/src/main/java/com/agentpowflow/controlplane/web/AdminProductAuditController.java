package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/v1/admin/product-audit")
public class AdminProductAuditController {
    private final ProductAssetService productAssetService;

    public AdminProductAuditController(ProductAssetService productAssetService) {
        this.productAssetService = productAssetService;
    }

    @GetMapping("/invocations")
    public ProductAssetDto.InvocationListResponse invocations(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)
                || !claims.roles().contains("PLATFORM_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "platform administrator role required");
        }
        return new ProductAssetDto.InvocationListResponse("ok", productAssetService.listPlatformInvocations());
    }
}
