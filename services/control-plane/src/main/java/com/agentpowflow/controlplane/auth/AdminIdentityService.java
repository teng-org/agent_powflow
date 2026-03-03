package com.agentpowflow.controlplane.auth;

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

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;

@Service
public class AdminIdentityService {
    private final TenantRepository tenantRepository;
    private final UserRepository userRepository;
    private final RoleRepository roleRepository;
    private final UserRoleRepository userRoleRepository;
    private final PasswordEncoder passwordEncoder;

    public AdminIdentityService(
            TenantRepository tenantRepository,
            UserRepository userRepository,
            RoleRepository roleRepository,
            UserRoleRepository userRoleRepository,
            PasswordEncoder passwordEncoder
    ) {
        this.tenantRepository = tenantRepository;
        this.userRepository = userRepository;
        this.roleRepository = roleRepository;
        this.userRoleRepository = userRoleRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional(readOnly = true)
    public List<TenantView> listTenants() {
        return tenantRepository.findAll().stream().map(t -> new TenantView(
                t.getTenantId(),
                t.getTenantCode(),
                t.getName(),
                t.getStatus(),
                t.getCreatedAt().toString(),
                t.getUpdatedAt().toString()
        )).toList();
    }

    @Transactional
    public TenantView createTenant(String tenantCode, String name) {
        String code = normalizeTenantCode(tenantCode);
        if (tenantRepository.findByTenantCode(code).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "tenant code exists");
        }
        TenantEntity tenant = new TenantEntity();
        tenant.setTenantCode(code);
        tenant.setName(name == null || name.isBlank() ? code : name.trim());
        tenant.setStatus("ACTIVE");
        tenant = tenantRepository.save(tenant);
        ensureRole(tenant.getTenantId(), "TENANT_ADMIN", "Tenant Admin");
        ensureRole(tenant.getTenantId(), "USER", "User");
        return new TenantView(
                tenant.getTenantId(),
                tenant.getTenantCode(),
                tenant.getName(),
                tenant.getStatus(),
                tenant.getCreatedAt().toString(),
                tenant.getUpdatedAt().toString()
        );
    }

    @Transactional(readOnly = true)
    public List<UserView> listUsers(String tenantId) {
        List<UserEntity> users = userRepository.findByTenantIdOrderByCreatedAtDesc(tenantId);
        List<UserView> out = new ArrayList<>();
        for (UserEntity u : users) {
            out.add(toView(u));
        }
        return out;
    }

    @Transactional
    public UserView createUser(String tenantId, String email, String password, String displayName, List<String> roles) {
        TenantEntity tenant = tenantRepository.findById(tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "tenant not found"));
        String normalizedEmail = normalizeEmail(email);
        if (userRepository.findByTenantIdAndEmail(tenantId, normalizedEmail).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "user already exists");
        }
        UserEntity user = new UserEntity();
        user.setTenantId(tenant.getTenantId());
        user.setEmail(normalizedEmail);
        user.setDisplayName(displayName == null || displayName.isBlank() ? normalizedEmail : displayName.trim());
        user.setPasswordHash(passwordEncoder.encode(password));
        user.setStatus("ACTIVE");
        user = userRepository.save(user);

        bindRoles(tenant.getTenantId(), user.getUserId(), roles == null || roles.isEmpty() ? List.of("USER") : roles);
        return toView(user);
    }

    @Transactional
    public UserView patchUser(String tenantId, String userId, String displayName, String status, List<String> roles) {
        UserEntity user = userRepository.findById(userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "user not found"));
        if (!tenantId.equals(user.getTenantId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "tenant mismatch");
        }
        if (displayName != null && !displayName.isBlank()) {
            user.setDisplayName(displayName.trim());
        }
        if (status != null && !status.isBlank()) {
            user.setStatus(status.trim().toUpperCase(Locale.ROOT));
        }
        userRepository.save(user);
        if (roles != null && !roles.isEmpty()) {
            bindRoles(tenantId, userId, roles);
        }
        return toView(user);
    }

    private UserView toView(UserEntity user) {
        List<UserRoleEntity> links = userRoleRepository.findByTenantIdAndUserId(user.getTenantId(), user.getUserId());
        Set<String> roleIds = links.stream().map(UserRoleEntity::getRoleId).collect(java.util.stream.Collectors.toSet());
        List<String> roles = roleRepository.findAllById(roleIds).stream().map(RoleEntity::getCode).toList();
        return new UserView(
                user.getUserId(),
                user.getTenantId(),
                user.getEmail(),
                user.getDisplayName(),
                user.getStatus(),
                roles,
                user.getCreatedAt().toString(),
                user.getUpdatedAt().toString()
        );
    }

    private void bindRoles(String tenantId, String userId, List<String> roles) {
        userRoleRepository.deleteByTenantIdAndUserId(tenantId, userId);
        for (String roleCode : roles) {
            String code = roleCode == null ? "" : roleCode.trim().toUpperCase(Locale.ROOT);
            if (code.isBlank()) {
                continue;
            }
            RoleEntity role = ensureRole(tenantId, code, code);
            UserRoleEntity link = new UserRoleEntity();
            link.setTenantId(tenantId);
            link.setUserId(userId);
            link.setRoleId(role.getRoleId());
            userRoleRepository.save(link);
        }
    }

    private RoleEntity ensureRole(String tenantId, String code, String name) {
        Optional<RoleEntity> existing = roleRepository.findByTenantIdAndCode(tenantId, code);
        if (existing.isPresent()) {
            return existing.get();
        }
        RoleEntity role = new RoleEntity();
        role.setTenantId(tenantId);
        role.setCode(code);
        role.setName(name);
        return roleRepository.save(role);
    }

    private String normalizeTenantCode(String tenantCode) {
        return tenantCode == null ? "" : tenantCode.trim().toLowerCase(Locale.ROOT);
    }

    private String normalizeEmail(String email) {
        return email == null ? "" : email.trim().toLowerCase(Locale.ROOT);
    }

    public record TenantView(
            String tenantId,
            String tenantCode,
            String name,
            String status,
            String createdAt,
            String updatedAt
    ) {
    }

    public record UserView(
            String userId,
            String tenantId,
            String email,
            String displayName,
            String status,
            List<String> roles,
            String createdAt,
            String updatedAt
    ) {
    }
}

