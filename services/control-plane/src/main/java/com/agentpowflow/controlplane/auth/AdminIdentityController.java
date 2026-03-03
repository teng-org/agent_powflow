package com.agentpowflow.controlplane.auth;

import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Set;

@RestController
@RequestMapping("/api/v1/admin")
public class AdminIdentityController {
    private final AdminIdentityService adminIdentityService;

    public AdminIdentityController(AdminIdentityService adminIdentityService) {
        this.adminIdentityService = adminIdentityService;
    }

    @GetMapping("/tenants")
    public Map<String, Object> listTenants() {
        return Map.of("status", "ok", "items", adminIdentityService.listTenants());
    }

    @PostMapping("/tenants")
    public Map<String, Object> createTenant(@RequestBody CreateTenantRequest body, Authentication authentication) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        requirePlatformAdmin(claims);
        return Map.of("status", "ok", "item", adminIdentityService.createTenant(body.tenantCode(), body.name()));
    }

    @GetMapping("/tenants/{tenantId}/users")
    public Map<String, Object> listUsers(@PathVariable String tenantId, Authentication authentication) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        requireTenantAccess(claims, tenantId);
        return Map.of("status", "ok", "items", adminIdentityService.listUsers(tenantId));
    }

    @PostMapping("/tenants/{tenantId}/users")
    public Map<String, Object> createUser(
            @PathVariable String tenantId,
            @RequestBody CreateUserRequest body,
            Authentication authentication
    ) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        requireTenantAccess(claims, tenantId);
        return Map.of(
                "status", "ok",
                "item", adminIdentityService.createUser(tenantId, body.email(), body.password(), body.displayName(), body.roles())
        );
    }

    @PatchMapping("/tenants/{tenantId}/users/{userId}")
    public Map<String, Object> patchUser(
            @PathVariable String tenantId,
            @PathVariable String userId,
            @RequestBody PatchUserRequest body,
            Authentication authentication
    ) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        requireTenantAccess(claims, tenantId);
        return Map.of(
                "status", "ok",
                "item", adminIdentityService.patchUser(tenantId, userId, body.displayName(), body.status(), body.roles())
        );
    }

    private void requirePlatformAdmin(TokenClaims claims) {
        if (!claims.roles().contains("PLATFORM_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "platform admin required");
        }
    }

    private void requireTenantAccess(TokenClaims claims, String tenantId) {
        Set<String> roles = Set.copyOf(claims.roles());
        if (roles.contains("PLATFORM_ADMIN")) {
            return;
        }
        if (!roles.contains("TENANT_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "tenant admin required");
        }
        if (!claims.tenantId().equals(tenantId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "tenant scope forbidden");
        }
    }

    public record CreateTenantRequest(
            @NotBlank String tenantCode,
            @NotBlank String name
    ) {
    }

    public record CreateUserRequest(
            @NotBlank String email,
            @NotBlank String password,
            String displayName,
            List<String> roles
    ) {
    }

    public record PatchUserRequest(
            String displayName,
            String status,
            List<String> roles
    ) {
    }
}

