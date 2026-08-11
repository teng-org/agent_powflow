package com.agentpowflow.controlplane.auth;

import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.validation.annotation.Validated;

import java.util.Map;
import java.util.Set;
import com.agentpowflow.controlplane.domain.UserRepository;

@RestController
@RequestMapping("/api/v1")
@Validated
public class WorkspaceMembershipController {
    private final WorkspaceMembershipService membershipService;
    private final AdminIdentityService adminIdentityService;
    private final UserRepository userRepository;

    public WorkspaceMembershipController(
            WorkspaceMembershipService membershipService,
            AdminIdentityService adminIdentityService,
            UserRepository userRepository
    ) {
        this.membershipService = membershipService;
        this.adminIdentityService = adminIdentityService;
        this.userRepository = userRepository;
    }

    @GetMapping("/workspace/members")
    public Map<String, Object> members(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        return Map.of("status", "ok", "items", adminIdentityService.listUsers(claims.tenantId()));
    }

    @PatchMapping("/workspace/members/{userId}")
    public Map<String, Object> patchMember(
            @PathVariable String userId,
            @RequestBody PatchMemberRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = requireTenantAdmin(authentication);
        var target = userRepository.findById(userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "workspace member not found"));
        if (!claims.tenantId().equals(target.getTenantId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workspace scope forbidden");
        }
        if (claims.sub().equalsIgnoreCase(target.getEmail())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "use another workspace administrator to change your own access");
        }
        if (request.roles() != null && request.roles().stream()
                .map(value -> value == null ? "" : value.toUpperCase(java.util.Locale.ROOT))
                .anyMatch(value -> !Set.of("USER", "TENANT_ADMIN").contains(value))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workspace roles must be USER or TENANT_ADMIN");
        }
        if (request.status() != null && !Set.of("ACTIVE", "DISABLED").contains(request.status().toUpperCase(java.util.Locale.ROOT))) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "member status must be ACTIVE or DISABLED");
        }
        return Map.of(
                "status", "ok",
                "item", adminIdentityService.patchUser(
                        claims.tenantId(), userId, request.displayName(), request.status(), request.roles()
                )
        );
    }

    @GetMapping("/workspace/invitations")
    public Map<String, Object> invitations(Authentication authentication) {
        TokenClaims claims = requireTenantAdmin(authentication);
        return Map.of("status", "ok", "items", membershipService.listInvitations(claims.tenantId()));
    }

    @PostMapping("/workspace/invitations")
    public Map<String, Object> invite(
            @RequestBody CreateInvitationRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = requireTenantAdmin(authentication);
        WorkspaceMembershipService.InvitationSecret result = membershipService.createInvitation(
                claims.tenantId(), claims.sub(), request.email(), request.roleCode(), request.expiresInDays()
        );
        return Map.of("status", "ok", "item", result.invitation(), "invitation_token", result.token());
    }

    @DeleteMapping("/workspace/invitations/{invitationId}")
    public Map<String, Object> revoke(
            @PathVariable String invitationId,
            Authentication authentication
    ) {
        TokenClaims claims = requireTenantAdmin(authentication);
        return Map.of(
                "status", "ok",
                "item", membershipService.revokeInvitation(claims.tenantId(), invitationId)
        );
    }

    @PostMapping("/auth/invitations/accept")
    public Map<String, Object> acceptLegacy(@RequestBody AcceptInvitationRequest request) {
        throw new ResponseStatusException(
                HttpStatus.GONE,
                "请先使用已有账户登录，再在登录页填写邀请令牌加入工作空间"
        );
    }

    @PostMapping("/workspace/invitations/accept")
    public Map<String, Object> accept(
            @RequestBody AcceptInvitationRequest request,
            Authentication authentication
    ) {
        TokenClaims claims = claims(authentication);
        WorkspaceMembershipService.AcceptanceResult result = membershipService.acceptInvitationForExistingUser(
                request.invitationToken(), claims.tenantId(), claims.sub()
        );
        return Map.of(
                "status", "ok",
                "tenant_code", result.tenantCode(),
                "email", result.email(),
                "role", result.roleCode()
        );
    }

    private TokenClaims requireTenantAdmin(Authentication authentication) {
        TokenClaims claims = claims(authentication);
        if (!claims.roles().contains("TENANT_ADMIN") && !claims.roles().contains("PLATFORM_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workspace administrator role required");
        }
        return claims;
    }

    private TokenClaims claims(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "authenticated token claims missing");
        }
        return claims;
    }

    public record CreateInvitationRequest(
            @NotBlank String email,
            String roleCode,
            Integer expiresInDays
    ) {}

    public record AcceptInvitationRequest(
            @NotBlank String invitationToken
    ) {}

    public record PatchMemberRequest(
            String displayName,
            String status,
            java.util.List<String> roles
    ) {}
}
