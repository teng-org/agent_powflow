package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.product.PlatformInstallationService;
import com.agentpowflow.controlplane.web.dto.PlatformSetupDto;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/v1")
public class PlatformSetupController {
    private final PlatformInstallationService installationService;

    public PlatformSetupController(PlatformInstallationService installationService) {
        this.installationService = installationService;
    }

    @GetMapping("/setup/status")
    public PlatformSetupDto.SetupResponse publicStatus() {
        return new PlatformSetupDto.SetupResponse("ok", installationService.status(false));
    }

    @PostMapping("/admin/setup/check")
    public PlatformSetupDto.SetupResponse check(Authentication authentication) {
        requirePlatformAdmin(authentication);
        return new PlatformSetupDto.SetupResponse("ok", installationService.status(true));
    }

    @PutMapping("/admin/setup/site")
    public PlatformSetupDto.SetupResponse site(
            @Valid @RequestBody PlatformSetupDto.SiteRequest request,
            Authentication authentication
    ) {
        requirePlatformAdmin(authentication);
        return new PlatformSetupDto.SetupResponse("ok", installationService.saveSite(request));
    }

    @PostMapping("/admin/setup/complete")
    public PlatformSetupDto.SetupResponse complete(Authentication authentication) {
        TokenClaims claims = requirePlatformAdmin(authentication);
        return new PlatformSetupDto.SetupResponse("ok", installationService.complete(claims.sub()));
    }

    private TokenClaims requirePlatformAdmin(Authentication authentication) {
        if (authentication == null || !(authentication.getPrincipal() instanceof TokenClaims claims)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "authenticated token claims missing");
        }
        if (!claims.roles().contains("PLATFORM_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "platform administrator role required");
        }
        return claims;
    }
}
