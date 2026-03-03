package com.agentpowflow.controlplane.auth;

import com.agentpowflow.controlplane.domain.RefreshTokenEntity;
import com.agentpowflow.controlplane.domain.RefreshTokenRepository;
import com.agentpowflow.controlplane.domain.RoleEntity;
import com.agentpowflow.controlplane.domain.RoleRepository;
import com.agentpowflow.controlplane.domain.TenantEntity;
import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.domain.UserEntity;
import com.agentpowflow.controlplane.domain.UserRepository;
import com.agentpowflow.controlplane.domain.UserRoleEntity;
import com.agentpowflow.controlplane.domain.UserRoleRepository;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;

@Service
public class IdentityService {
    private final AuthProperties authProperties;
    private final TokenService tokenService;
    private final TenantRepository tenantRepository;
    private final UserRepository userRepository;
    private final RoleRepository roleRepository;
    private final UserRoleRepository userRoleRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final PasswordEncoder passwordEncoder;

    public IdentityService(
            AuthProperties authProperties,
            TokenService tokenService,
            TenantRepository tenantRepository,
            UserRepository userRepository,
            RoleRepository roleRepository,
            UserRoleRepository userRoleRepository,
            RefreshTokenRepository refreshTokenRepository,
            PasswordEncoder passwordEncoder
    ) {
        this.authProperties = authProperties;
        this.tokenService = tokenService;
        this.tenantRepository = tenantRepository;
        this.userRepository = userRepository;
        this.roleRepository = roleRepository;
        this.userRoleRepository = userRoleRepository;
        this.refreshTokenRepository = refreshTokenRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional
    public AuthResult register(String tenantCode, String email, String password, String displayName) {
        String code = normalizeTenantCode(tenantCode);
        TenantEntity tenant = tenantRepository.findByTenantCode(code)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "tenant not found"));
        if (!"ACTIVE".equalsIgnoreCase(tenant.getStatus())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "tenant disabled");
        }
        String normalizedEmail = normalizeEmail(email);
        if (userRepository.findByTenantIdAndEmail(tenant.getTenantId(), normalizedEmail).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "user already exists");
        }
        UserEntity user = new UserEntity();
        user.setTenantId(tenant.getTenantId());
        user.setEmail(normalizedEmail);
        user.setDisplayName(displayName == null || displayName.isBlank() ? normalizedEmail : displayName.trim());
        user.setPasswordHash(passwordEncoder.encode(password));
        user.setStatus("ACTIVE");
        user = userRepository.save(user);
        ensureRoleAndBind(tenant.getTenantId(), user.getUserId(), "USER", "User");
        return issueTokens(tenant.getTenantId(), user, List.of("USER"));
    }

    @Transactional
    public AuthResult login(String tenantCode, String identity, String password) {
        // Legacy fallback for existing local admin workflow.
        if (identity != null
                && identity.equals(authProperties.getAdminUsername())
                && authProperties.getAdminPassword().equals(password)) {
            String tenantId = authProperties.getAdminTenantId();
            return issueTokensWithoutUser(tenantId, authProperties.getAdminUsername(), List.of(authProperties.getAdminRole()));
        }

        String code = normalizeTenantCode(tenantCode);
        TenantEntity tenant = tenantRepository.findByTenantCode(code)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid credentials"));
        String email = normalizeEmail(identity);
        UserEntity user = userRepository.findByTenantIdAndEmail(tenant.getTenantId(), email)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid credentials"));
        if (!"ACTIVE".equalsIgnoreCase(user.getStatus())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "user disabled");
        }
        if (!passwordEncoder.matches(password, user.getPasswordHash())) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid credentials");
        }
        user.setLastLoginAt(Instant.now());
        userRepository.save(user);
        List<String> roles = loadUserRoles(tenant.getTenantId(), user.getUserId());
        if (roles.isEmpty()) {
            roles = List.of("USER");
        }
        return issueTokens(tenant.getTenantId(), user, roles);
    }

    @Transactional
    public AuthResult refresh(String refreshToken) {
        TokenClaims claims = tokenService.verify(refreshToken, "refresh");
        String hash = tokenHash(refreshToken);
        RefreshTokenEntity row = refreshTokenRepository.findByTokenHash(hash)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "refresh token invalid"));
        if (row.isRevoked() || row.getExpiresAt().isBefore(Instant.now())) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "refresh token expired");
        }
        Optional<UserEntity> maybeUser = userRepository.findById(row.getUserId());
        if (maybeUser.isEmpty()) {
            return issueTokensWithoutUser(claims.tenantId(), claims.sub(), claims.roles());
        }
        UserEntity user = maybeUser.get();
        List<String> roles = loadUserRoles(row.getTenantId(), row.getUserId());
        if (roles.isEmpty()) {
            roles = claims.roles();
        }
        return issueTokens(row.getTenantId(), user, roles);
    }

    @Transactional
    public void logout(String refreshToken) {
        if (refreshToken == null || refreshToken.isBlank()) {
            return;
        }
        String hash = tokenHash(refreshToken);
        refreshTokenRepository.findByTokenHash(hash).ifPresent(token -> {
            token.setRevoked(true);
            refreshTokenRepository.save(token);
        });
    }

    @Transactional(readOnly = true)
    public MeView me(TokenClaims claims) {
        Optional<UserEntity> maybe = userRepository.findByTenantIdAndEmail(claims.tenantId(), normalizeEmail(claims.sub()));
        if (maybe.isEmpty()) {
            return new MeView(claims.sub(), claims.tenantId(), claims.roles(), claims.sub(), "ACTIVE");
        }
        UserEntity user = maybe.get();
        List<String> roles = loadUserRoles(user.getTenantId(), user.getUserId());
        return new MeView(user.getEmail(), user.getTenantId(), roles, user.getDisplayName(), user.getStatus());
    }

    @Transactional
    public MeView updateMe(TokenClaims claims, String displayName) {
        UserEntity user = userRepository.findByTenantIdAndEmail(claims.tenantId(), normalizeEmail(claims.sub()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "user not found"));
        user.setDisplayName(displayName == null || displayName.isBlank() ? user.getDisplayName() : displayName.trim());
        userRepository.save(user);
        List<String> roles = loadUserRoles(user.getTenantId(), user.getUserId());
        return new MeView(user.getEmail(), user.getTenantId(), roles, user.getDisplayName(), user.getStatus());
    }

    private AuthResult issueTokens(String tenantId, UserEntity user, List<String> roles) {
        return issueTokensWithoutUser(tenantId, user.getEmail(), roles);
    }

    private AuthResult issueTokensWithoutUser(String tenantId, String subject, List<String> roles) {
        String access = tokenService.signAccessToken(subject, tenantId, roles);
        String refresh = tokenService.signRefreshToken(subject, tenantId, roles);

        RefreshTokenEntity row = new RefreshTokenEntity();
        row.setTenantId(tenantId);
        Optional<UserEntity> maybeUser = userRepository.findByTenantIdAndEmail(tenantId, normalizeEmail(subject));
        row.setUserId(maybeUser.map(UserEntity::getUserId).orElse(subject));
        row.setTokenHash(tokenHash(refresh));
        row.setExpiresAt(Instant.now().plusSeconds(Math.max(600L, authProperties.getRefreshTtlSeconds())));
        row.setRevoked(false);
        refreshTokenRepository.save(row);

        return new AuthResult(
                access,
                refresh,
                "Bearer",
                authProperties.getAccessTtlSeconds(),
                tenantId,
                subject,
                roles
        );
    }

    private List<String> loadUserRoles(String tenantId, String userId) {
        List<UserRoleEntity> links = userRoleRepository.findByTenantIdAndUserId(tenantId, userId);
        if (links.isEmpty()) {
            return List.of();
        }
        Set<String> roleIds = links.stream().map(UserRoleEntity::getRoleId).collect(java.util.stream.Collectors.toSet());
        List<RoleEntity> roles = roleRepository.findAllById(roleIds);
        List<String> out = new ArrayList<>();
        for (RoleEntity role : roles) {
            out.add(role.getCode());
        }
        return out;
    }

    private void ensureRoleAndBind(String tenantId, String userId, String roleCode, String roleName) {
        RoleEntity role = roleRepository.findByTenantIdAndCode(tenantId, roleCode).orElseGet(() -> {
            RoleEntity created = new RoleEntity();
            created.setTenantId(tenantId);
            created.setCode(roleCode);
            created.setName(roleName);
            return roleRepository.save(created);
        });
        UserRoleEntity link = new UserRoleEntity();
        link.setTenantId(tenantId);
        link.setUserId(userId);
        link.setRoleId(role.getRoleId());
        userRoleRepository.save(link);
    }

    private String normalizeTenantCode(String tenantCode) {
        String code = tenantCode == null || tenantCode.isBlank() ? authProperties.getAdminTenantId() : tenantCode.trim();
        return code.toLowerCase(Locale.ROOT);
    }

    private String normalizeEmail(String email) {
        if (email == null) {
            return "";
        }
        return email.trim().toLowerCase(Locale.ROOT);
    }

    private String tokenHash(String token) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(token.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes);
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "token hash failed");
        }
    }

    public record AuthResult(
            String accessToken,
            String refreshToken,
            String tokenType,
            long expiresIn,
            String tenantId,
            String actorId,
            List<String> roles
    ) {
    }

    public record MeView(
            String email,
            String tenantId,
            List<String> roles,
            String displayName,
            String status
    ) {
    }
}

