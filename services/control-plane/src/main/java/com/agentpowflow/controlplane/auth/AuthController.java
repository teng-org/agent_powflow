package com.agentpowflow.controlplane.auth;

import jakarta.validation.constraints.NotBlank;
import org.springframework.security.core.Authentication;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1")
@Validated
public class AuthController {
    private final IdentityService identityService;

    public AuthController(IdentityService identityService) {
        this.identityService = identityService;
    }

    @PostMapping("/auth/register")
    public LoginResponse register(@RequestBody RegisterRequest body) {
        IdentityService.AuthResult result = identityService.register(
                body.tenantCode(),
                body.workspaceName(),
                body.email(),
                body.password(),
                body.displayName()
        );
        return toLoginResponse(result);
    }

    @PostMapping("/auth/login")
    public ResponseEntity<?> login(@RequestBody LoginRequest body) {
        String identity = body.email() != null && !body.email().isBlank() ? body.email() : body.username();
        try {
            IdentityService.AuthResult result = identityService.login(body.tenantCode(), identity, body.password());
            return ResponseEntity.ok(toLoginResponse(result));
        } catch (IdentityService.WorkspaceSelectionRequiredException ex) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(new WorkspaceSelectionResponse(
                    "workspace_selection_required",
                    "WORKSPACE_SELECTION_REQUIRED",
                    "请选择要进入的工作空间",
                    ex.candidates()
            ));
        }
    }

    @PostMapping("/auth/refresh")
    public RefreshResponse refresh(@RequestBody RefreshRequest body) {
        IdentityService.AuthResult result = identityService.refresh(body.refreshToken());
        return new RefreshResponse(
                "ok", result.accessToken(), result.refreshToken(), result.tokenType(), result.expiresIn()
        );
    }

    @PostMapping("/auth/logout")
    public Map<String, Object> logout(@RequestBody(required = false) LogoutRequest body) {
        if (body != null && body.refreshToken() != null && !body.refreshToken().isBlank()) {
            identityService.logout(body.refreshToken());
        }
        Map<String, Object> out = new HashMap<>();
        out.put("status", "ok");
        out.put("ts", Instant.now().toString());
        return out;
    }

    @GetMapping("/auth/me")
    public Map<String, Object> me(Authentication authentication) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        IdentityService.MeView me = identityService.me(claims);
        return Map.of(
                "status", "ok",
                "item", Map.of(
                        "email", me.email(),
                        "tenant_id", me.tenantId(),
                        "roles", me.roles(),
                        "display_name", me.displayName(),
                        "user_status", me.status()
                )
        );
    }

    @PatchMapping("/me")
    public Map<String, Object> patchMe(@RequestBody PatchMeRequest body, Authentication authentication) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        IdentityService.MeView me = identityService.updateMe(claims, body.displayName());
        return Map.of(
                "status", "ok",
                "item", Map.of(
                        "email", me.email(),
                        "tenant_id", me.tenantId(),
                        "roles", me.roles(),
                        "display_name", me.displayName(),
                        "user_status", me.status()
                )
        );
    }

    private LoginResponse toLoginResponse(IdentityService.AuthResult result) {
        String role = result.roles().isEmpty() ? "USER" : result.roles().getFirst();
        return new LoginResponse(
                "ok",
                result.accessToken(),
                result.refreshToken(),
                result.tokenType(),
                result.expiresIn(),
                role,
                result.tenantId(),
                result.tenantCode(),
                result.actorId(),
                result.roles()
        );
    }

    public record RegisterRequest(
            @NotBlank String tenantCode,
            String workspaceName,
            @NotBlank String email,
            @NotBlank String password,
            String displayName
    ) {
    }

    public record LoginRequest(
            String tenantCode,
            String username,
            String email,
            @NotBlank String password
    ) {
    }

    public record RefreshRequest(
            @NotBlank String refreshToken
    ) {
    }

    public record LogoutRequest(
            String refreshToken
    ) {
    }

    public record PatchMeRequest(
            String displayName
    ) {
    }

    public record LoginResponse(
            String status,
            String accessToken,
            String refreshToken,
            String tokenType,
            long expiresIn,
            String role,
            String tenantId,
            String tenantCode,
            String actorId,
            List<String> roles
    ) {
    }

    public record WorkspaceSelectionResponse(
            String status,
            String code,
            String message,
            List<IdentityService.WorkspaceCandidate> items
    ) {
    }

    public record RefreshResponse(
            String status,
            String accessToken,
            String refreshToken,
            String tokenType,
            long expiresIn
    ) {
    }
}
