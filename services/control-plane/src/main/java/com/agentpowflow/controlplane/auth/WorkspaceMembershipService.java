package com.agentpowflow.controlplane.auth;

import com.agentpowflow.controlplane.domain.RoleEntity;
import com.agentpowflow.controlplane.domain.RoleRepository;
import com.agentpowflow.controlplane.domain.TenantEntity;
import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.domain.UserEntity;
import com.agentpowflow.controlplane.domain.UserRepository;
import com.agentpowflow.controlplane.domain.UserRoleEntity;
import com.agentpowflow.controlplane.domain.UserRoleRepository;
import com.agentpowflow.controlplane.domain.WorkspaceInvitationEntity;
import com.agentpowflow.controlplane.domain.WorkspaceInvitationRepository;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;

@Service
public class WorkspaceMembershipService {
    private final WorkspaceInvitationRepository invitationRepository;
    private final TenantRepository tenantRepository;
    private final UserRepository userRepository;
    private final RoleRepository roleRepository;
    private final UserRoleRepository userRoleRepository;
    private final PasswordEncoder passwordEncoder;
    private final SecureRandom secureRandom = new SecureRandom();

    public WorkspaceMembershipService(
            WorkspaceInvitationRepository invitationRepository,
            TenantRepository tenantRepository,
            UserRepository userRepository,
            RoleRepository roleRepository,
            UserRoleRepository userRoleRepository,
            PasswordEncoder passwordEncoder
    ) {
        this.invitationRepository = invitationRepository;
        this.tenantRepository = tenantRepository;
        this.userRepository = userRepository;
        this.roleRepository = roleRepository;
        this.userRoleRepository = userRoleRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional
    public InvitationSecret createInvitation(
            String tenantId,
            String invitedBy,
            String email,
            String roleCode,
            Integer expiresInDays
    ) {
        TenantEntity tenant = activeTenant(tenantId);
        String normalizedEmail = normalizeEmail(email);
        if (normalizedEmail.isBlank() || !normalizedEmail.contains("@")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "valid invitation email required");
        }
        if (userRepository.findByTenantIdAndEmail(tenantId, normalizedEmail).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "user is already a workspace member");
        }
        String normalizedRole = normalizeInvitableRole(roleCode);
        Instant now = Instant.now();
        for (WorkspaceInvitationEntity existing : invitationRepository.findByTenantIdAndEmailAndStatus(
                tenantId, normalizedEmail, "PENDING")) {
            existing.setStatus("REVOKED");
            existing.setRevokedAt(now);
            invitationRepository.save(existing);
        }

        byte[] secretBytes = new byte[32];
        secureRandom.nextBytes(secretBytes);
        String secretPart = Base64.getUrlEncoder().withoutPadding().encodeToString(secretBytes);
        String prefix = secretPart.substring(0, 10);
        String plaintext = "pwi_" + prefix + "." + secretPart;
        int days = expiresInDays == null ? 7 : Math.max(1, Math.min(expiresInDays, 30));

        WorkspaceInvitationEntity invitation = new WorkspaceInvitationEntity();
        invitation.setTenantId(tenant.getTenantId());
        invitation.setEmail(normalizedEmail);
        invitation.setRoleCode(normalizedRole);
        invitation.setTokenPrefix("pwi_" + prefix);
        invitation.setTokenHash(hash(plaintext));
        invitation.setStatus("PENDING");
        invitation.setInvitedBy(invitedBy);
        invitation.setExpiresAt(now.plus(days, ChronoUnit.DAYS));
        invitation = invitationRepository.save(invitation);
        return new InvitationSecret(toView(invitation), plaintext);
    }

    @Transactional(readOnly = true)
    public List<InvitationView> listInvitations(String tenantId) {
        return invitationRepository.findByTenantIdOrderByCreatedAtDesc(tenantId).stream().map(this::toView).toList();
    }

    @Transactional
    public InvitationView revokeInvitation(String tenantId, String invitationId) {
        WorkspaceInvitationEntity invitation = invitationRepository.findById(invitationId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "invitation not found"));
        if (!tenantId.equals(invitation.getTenantId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workspace scope forbidden");
        }
        if ("PENDING".equals(invitation.getStatus())) {
            invitation.setStatus("REVOKED");
            invitation.setRevokedAt(Instant.now());
            invitationRepository.save(invitation);
        }
        return toView(invitation);
    }

    @Transactional
    public AcceptanceResult acceptInvitationForExistingUser(
            String plaintextToken,
            String sourceTenantId,
            String authenticatedEmail
    ) {
        if (plaintextToken == null || !plaintextToken.startsWith("pwi_")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid invitation token");
        }
        String normalizedEmail = normalizeEmail(authenticatedEmail);
        UserEntity sourceUser = userRepository.findByTenantIdAndEmail(sourceTenantId, normalizedEmail)
                .filter(user -> "ACTIVE".equalsIgnoreCase(user.getStatus()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "active account required"));
        WorkspaceInvitationEntity invitation = invitationRepository.findForUpdateByTokenHash(hash(plaintextToken))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "invitation not found"));
        if (!"PENDING".equals(invitation.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "invitation is no longer available");
        }
        if (invitation.getExpiresAt().isBefore(Instant.now())) {
            throw new ResponseStatusException(HttpStatus.GONE, "invitation expired");
        }
        if (!MessageDigest.isEqual(
                invitation.getEmail().getBytes(StandardCharsets.UTF_8),
                normalizedEmail.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "invitation email mismatch");
        }
        TenantEntity tenant = activeTenant(invitation.getTenantId());
        if (userRepository.findByTenantIdAndEmail(tenant.getTenantId(), normalizedEmail).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "user is already a workspace member");
        }

        UserEntity user = new UserEntity();
        user.setTenantId(tenant.getTenantId());
        user.setEmail(normalizedEmail);
        user.setDisplayName(sourceUser.getDisplayName());
        user.setPasswordHash(sourceUser.getPasswordHash());
        user.setStatus("ACTIVE");
        user = userRepository.save(user);
        bindRole(tenant.getTenantId(), user.getUserId(), "USER", "User");
        if ("TENANT_ADMIN".equals(invitation.getRoleCode())) {
            bindRole(tenant.getTenantId(), user.getUserId(), "TENANT_ADMIN", "Tenant Admin");
        }

        invitation.setStatus("ACCEPTED");
        invitation.setAcceptedBy(user.getEmail());
        invitation.setAcceptedAt(Instant.now());
        invitationRepository.save(invitation);
        return new AcceptanceResult(tenant.getTenantCode(), user.getEmail(), invitation.getRoleCode());
    }

    private TenantEntity activeTenant(String tenantId) {
        TenantEntity tenant = tenantRepository.findById(tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "workspace not found"));
        if (!"ACTIVE".equalsIgnoreCase(tenant.getStatus())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workspace disabled");
        }
        return tenant;
    }

    private void bindRole(String tenantId, String userId, String code, String name) {
        RoleEntity role = roleRepository.findByTenantIdAndCode(tenantId, code).orElseGet(() -> {
            RoleEntity created = new RoleEntity();
            created.setTenantId(tenantId);
            created.setCode(code);
            created.setName(name);
            return roleRepository.save(created);
        });
        UserRoleEntity link = new UserRoleEntity();
        link.setTenantId(tenantId);
        link.setUserId(userId);
        link.setRoleId(role.getRoleId());
        userRoleRepository.save(link);
    }

    private String normalizeInvitableRole(String roleCode) {
        String role = roleCode == null ? "USER" : roleCode.trim().toUpperCase(Locale.ROOT);
        if (!role.equals("USER") && !role.equals("TENANT_ADMIN")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invitation role must be USER or TENANT_ADMIN");
        }
        return role;
    }

    private String normalizeEmail(String email) {
        return email == null ? "" : email.trim().toLowerCase(Locale.ROOT);
    }

    private String hash(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "invitation token hash failed");
        }
    }

    private InvitationView toView(WorkspaceInvitationEntity invitation) {
        return new InvitationView(
                invitation.getInvitationId(), invitation.getEmail(), invitation.getRoleCode(),
                invitation.getTokenPrefix(), invitation.getStatus(), invitation.getInvitedBy(),
                invitation.getExpiresAt().toString(), invitation.getCreatedAt().toString(),
                invitation.getAcceptedAt() == null ? null : invitation.getAcceptedAt().toString()
        );
    }

    public record InvitationView(
            String invitationId, String email, String roleCode, String tokenPrefix, String status,
            String invitedBy, String expiresAt, String createdAt, String acceptedAt
    ) {}

    public record InvitationSecret(InvitationView invitation, String token) {}
    public record AcceptanceResult(String tenantCode, String email, String roleCode) {}
}
