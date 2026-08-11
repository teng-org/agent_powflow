package com.agentpowflow.controlplane.auth;

import com.agentpowflow.controlplane.domain.RefreshTokenEntity;
import com.agentpowflow.controlplane.domain.RefreshTokenRepository;
import com.agentpowflow.controlplane.domain.RoleRepository;
import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.domain.TenantEntity;
import com.agentpowflow.controlplane.domain.UserEntity;
import com.agentpowflow.controlplane.domain.UserRepository;
import com.agentpowflow.controlplane.domain.UserRoleRepository;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class IdentityServiceTest {

    @Test
    void loginWithoutWorkspaceResolvesTheOnlyMatchingMembership() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        RefreshTokenRepository refreshTokens = mock(RefreshTokenRepository.class);
        PasswordEncoder passwords = mock(PasswordEncoder.class);
        TenantEntity tenant = tenant("acme-uuid", "acme", "Acme AI");
        UserEntity user = user("user-uuid", "acme-uuid", "owner@acme.test", "hash-a");
        when(users.findByEmailOrderByCreatedAtDesc("owner@acme.test")).thenReturn(List.of(user));
        when(tenants.findById("acme-uuid")).thenReturn(Optional.of(tenant));
        when(users.findByTenantIdAndEmail("acme-uuid", "owner@acme.test")).thenReturn(Optional.of(user));
        when(passwords.matches("Password!2026", "hash-a")).thenReturn(true);
        when(refreshTokens.save(any(RefreshTokenEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));

        IdentityService service = new IdentityService(
                properties,
                new TokenService(properties),
                tenants,
                users,
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                refreshTokens,
                passwords
        );

        IdentityService.AuthResult result = service.login(null, "owner@acme.test", "Password!2026");

        assertEquals("acme-uuid", result.tenantId());
        assertEquals("acme", result.tenantCode());
    }

    @Test
    void loginWithoutWorkspaceRequestsSelectionWhenCredentialsMatchMultipleMemberships() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        PasswordEncoder passwords = mock(PasswordEncoder.class);
        TenantEntity acme = tenant("acme-uuid", "acme", "Acme AI");
        TenantEntity labs = tenant("labs-uuid", "labs", "Labs");
        UserEntity acmeUser = user("user-a", "acme-uuid", "member@example.test", "shared-hash");
        UserEntity labsUser = user("user-b", "labs-uuid", "member@example.test", "shared-hash");
        when(users.findByEmailOrderByCreatedAtDesc("member@example.test"))
                .thenReturn(List.of(acmeUser, labsUser));
        when(tenants.findById("acme-uuid")).thenReturn(Optional.of(acme));
        when(tenants.findById("labs-uuid")).thenReturn(Optional.of(labs));
        when(passwords.matches("Password!2026", "shared-hash")).thenReturn(true);

        IdentityService service = new IdentityService(
                properties,
                new TokenService(properties),
                tenants,
                users,
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                mock(RefreshTokenRepository.class),
                passwords
        );

        IdentityService.WorkspaceSelectionRequiredException error = assertThrows(
                IdentityService.WorkspaceSelectionRequiredException.class,
                () -> service.login("", "member@example.test", "Password!2026")
        );

        assertEquals(List.of("acme", "labs"), error.candidates().stream()
                .map(IdentityService.WorkspaceCandidate::tenantCode).toList());
    }

    @Test
    void legacyPlatformAdminLoginUsesPersistedTenantId() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        RefreshTokenRepository refreshTokens = mock(RefreshTokenRepository.class);
        TenantEntity platform = new TenantEntity();
        platform.setTenantId("platform-uuid");
        platform.setTenantCode("platform");
        UserEntity admin = new UserEntity();
        admin.setUserId("admin-uuid");
        admin.setTenantId("platform-uuid");
        admin.setEmail("admin");
        admin.setStatus("ACTIVE");
        when(tenants.findByTenantCode("platform")).thenReturn(Optional.of(platform));
        when(users.findByTenantIdAndEmail("platform-uuid", "admin")).thenReturn(Optional.of(admin));
        when(refreshTokens.save(any(RefreshTokenEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));

        IdentityService service = new IdentityService(
                properties,
                new TokenService(properties),
                tenants,
                users,
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                refreshTokens,
                mock(PasswordEncoder.class)
        );

        IdentityService.AuthResult result = service.login("platform", "admin", "admin123");

        assertEquals("platform-uuid", result.tenantId());
        assertEquals("platform-uuid", new TokenService(properties)
                .verify(result.accessToken(), "access").tenantId());
    }

    @Test
    void publicRegistrationCannotJoinAnExistingWorkspace() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        TenantRepository tenants = mock(TenantRepository.class);
        TenantEntity existing = new TenantEntity();
        existing.setTenantCode("acme");
        existing.setName("Acme");
        when(tenants.findByTenantCode("acme")).thenReturn(Optional.of(existing));

        IdentityService service = new IdentityService(
                properties,
                new TokenService(properties),
                tenants,
                mock(UserRepository.class),
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                mock(RefreshTokenRepository.class),
                mock(PasswordEncoder.class)
        );

        var error = assertThrows(
                org.springframework.web.server.ResponseStatusException.class,
                () -> service.register("acme", "Acme", "owner@acme.test", "Password!2026", "Owner")
        );
        assertEquals(409, error.getStatusCode().value());
    }

    @Test
    void publicRegistrationCannotCreateThePlatformWorkspace() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        IdentityService service = new IdentityService(
                properties,
                new TokenService(properties),
                mock(TenantRepository.class),
                mock(UserRepository.class),
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                mock(RefreshTokenRepository.class),
                mock(PasswordEncoder.class)
        );

        var error = assertThrows(
                org.springframework.web.server.ResponseStatusException.class,
                () -> service.register("platform", "Platform", "user@example.test", "Password!2026", "User")
        );
        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void refreshAtomicallyRevokesOldTokenAndReturnsPersistedReplacement() throws Exception {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret("control-primary-value-with-at-least-32-characters");
        properties.setJwtKeyId("control-current");
        TokenService tokenService = new TokenService(properties);

        RefreshTokenRepository refreshTokens = mock(RefreshTokenRepository.class);
        UserRepository users = mock(UserRepository.class);
        String original = tokenService.signRefreshToken("admin", "platform", List.of("PLATFORM_ADMIN"));
        RefreshTokenEntity stored = new RefreshTokenEntity();
        stored.setTenantId("platform");
        stored.setUserId("admin");
        stored.setTokenHash(hash(original));
        stored.setExpiresAt(Instant.now().plusSeconds(3600));
        stored.setRevoked(false);
        when(refreshTokens.findForUpdateByTokenHash(hash(original))).thenReturn(Optional.of(stored));
        when(refreshTokens.save(any(RefreshTokenEntity.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(users.findById("admin")).thenReturn(Optional.empty());
        when(users.findByTenantIdAndEmail("platform", "admin")).thenReturn(Optional.empty());

        IdentityService service = new IdentityService(
                properties,
                tokenService,
                mock(TenantRepository.class),
                users,
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                refreshTokens,
                mock(PasswordEncoder.class)
        );
        IdentityService.AuthResult result = service.refresh(original);

        assertTrue(stored.isRevoked());
        assertNotEquals(original, result.refreshToken());
        assertNotEquals(hash(original), hash(result.refreshToken()));
        assertTrue(result.refreshToken().split("\\.").length == 3);
        verify(refreshTokens).findForUpdateByTokenHash(hash(original));
        verify(refreshTokens, times(2)).save(any(RefreshTokenEntity.class));
    }

    private static String hash(String value) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))
        );
    }

    private static TenantEntity tenant(String id, String code, String name) {
        TenantEntity tenant = new TenantEntity();
        tenant.setTenantId(id);
        tenant.setTenantCode(code);
        tenant.setName(name);
        tenant.setStatus("ACTIVE");
        return tenant;
    }

    private static UserEntity user(String id, String tenantId, String email, String passwordHash) {
        UserEntity user = new UserEntity();
        user.setUserId(id);
        user.setTenantId(tenantId);
        user.setEmail(email);
        user.setDisplayName(email);
        user.setPasswordHash(passwordHash);
        user.setStatus("ACTIVE");
        return user;
    }
}
