package com.agentpowflow.controlplane.auth;

import com.agentpowflow.controlplane.domain.RoleEntity;
import com.agentpowflow.controlplane.domain.RoleRepository;
import com.agentpowflow.controlplane.domain.TenantEntity;
import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.domain.UserEntity;
import com.agentpowflow.controlplane.domain.UserRepository;
import com.agentpowflow.controlplane.domain.UserRoleEntity;
import com.agentpowflow.controlplane.domain.UserRoleRepository;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
public class AuthBootstrap implements CommandLineRunner {
    private final AuthProperties authProperties;
    private final TenantRepository tenantRepository;
    private final UserRepository userRepository;
    private final RoleRepository roleRepository;
    private final UserRoleRepository userRoleRepository;
    private final PasswordEncoder passwordEncoder;

    public AuthBootstrap(
            AuthProperties authProperties,
            TenantRepository tenantRepository,
            UserRepository userRepository,
            RoleRepository roleRepository,
            UserRoleRepository userRoleRepository,
            PasswordEncoder passwordEncoder
    ) {
        this.authProperties = authProperties;
        this.tenantRepository = tenantRepository;
        this.userRepository = userRepository;
        this.roleRepository = roleRepository;
        this.userRoleRepository = userRoleRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    public void run(String... args) {
        String tenantCode = authProperties.getAdminTenantId().toLowerCase();
        TenantEntity tenant = tenantRepository.findByTenantCode(tenantCode).orElseGet(() -> {
            TenantEntity t = new TenantEntity();
            t.setTenantCode(tenantCode);
            t.setName(tenantCode);
            t.setStatus("ACTIVE");
            return tenantRepository.save(t);
        });

        RoleEntity adminRole = ensureRole(tenant.getTenantId(), "PLATFORM_ADMIN", "Platform Admin");
        ensureRole(tenant.getTenantId(), "TENANT_ADMIN", "Tenant Admin");
        ensureRole(tenant.getTenantId(), "USER", "User");

        String adminEmail = authProperties.getAdminUsername().toLowerCase();
        UserEntity adminUser = userRepository.findByTenantIdAndEmail(tenant.getTenantId(), adminEmail).orElseGet(() -> {
            UserEntity u = new UserEntity();
            u.setTenantId(tenant.getTenantId());
            u.setEmail(adminEmail);
            u.setDisplayName("Admin");
            u.setPasswordHash(passwordEncoder.encode(authProperties.getAdminPassword()));
            u.setStatus("ACTIVE");
            return userRepository.save(u);
        });
        if (!passwordEncoder.matches(authProperties.getAdminPassword(), adminUser.getPasswordHash())) {
            adminUser.setPasswordHash(passwordEncoder.encode(authProperties.getAdminPassword()));
            adminUser.setStatus("ACTIVE");
            adminUser = userRepository.save(adminUser);
        }

        boolean linked = userRoleRepository.findByTenantIdAndUserId(tenant.getTenantId(), adminUser.getUserId())
                .stream()
                .anyMatch(x -> adminRole.getRoleId().equals(x.getRoleId()));
        if (!linked) {
            UserRoleEntity ur = new UserRoleEntity();
            ur.setTenantId(tenant.getTenantId());
            ur.setUserId(adminUser.getUserId());
            ur.setRoleId(adminRole.getRoleId());
            userRoleRepository.save(ur);
        }
    }

    private RoleEntity ensureRole(String tenantId, String code, String name) {
        return roleRepository.findByTenantIdAndCode(tenantId, code).orElseGet(() -> {
            RoleEntity r = new RoleEntity();
            r.setTenantId(tenantId);
            r.setCode(code);
            r.setName(name);
            return roleRepository.save(r);
        });
    }
}
