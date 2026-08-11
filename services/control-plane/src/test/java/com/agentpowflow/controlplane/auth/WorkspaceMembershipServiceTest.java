package com.agentpowflow.controlplane.auth;

import com.agentpowflow.controlplane.domain.RoleRepository;
import com.agentpowflow.controlplane.domain.RoleEntity;
import com.agentpowflow.controlplane.domain.TenantEntity;
import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.domain.UserRepository;
import com.agentpowflow.controlplane.domain.UserEntity;
import com.agentpowflow.controlplane.domain.UserRoleRepository;
import com.agentpowflow.controlplane.domain.WorkspaceInvitationEntity;
import com.agentpowflow.controlplane.domain.WorkspaceInvitationRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkspaceMembershipServiceTest {

    @Test
    void invitationSecretIsReturnedOnceWhileOnlyHashIsPersisted() {
        WorkspaceInvitationRepository invitations = mock(WorkspaceInvitationRepository.class);
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        TenantEntity tenant = activeTenant();
        when(tenants.findById("tenant-1")).thenReturn(Optional.of(tenant));
        when(users.findByTenantIdAndEmail("tenant-1", "member@example.com")).thenReturn(Optional.empty());
        when(invitations.findByTenantIdAndEmailAndStatus("tenant-1", "member@example.com", "PENDING"))
                .thenReturn(List.of());
        when(invitations.save(any(WorkspaceInvitationEntity.class))).thenAnswer(call -> {
            WorkspaceInvitationEntity value = call.getArgument(0);
            value.prePersist();
            return value;
        });

        WorkspaceMembershipService service = service(invitations, tenants, users);
        WorkspaceMembershipService.InvitationSecret result = service.createInvitation(
                "tenant-1", "owner@example.com", "Member@Example.com", "USER", 7
        );

        assertTrue(result.token().startsWith("pwi_"));
        ArgumentCaptor<WorkspaceInvitationEntity> captor = ArgumentCaptor.forClass(WorkspaceInvitationEntity.class);
        verify(invitations).save(captor.capture());
        assertEquals(64, captor.getValue().getTokenHash().length());
        assertFalse(captor.getValue().getTokenHash().contains(result.token()));
        assertEquals("member@example.com", result.invitation().email());
    }

    @Test
    void invitationRejectsPrivilegeOutsideWorkspaceRoles() {
        WorkspaceInvitationRepository invitations = mock(WorkspaceInvitationRepository.class);
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        when(tenants.findById("tenant-1")).thenReturn(Optional.of(activeTenant()));
        when(users.findByTenantIdAndEmail("tenant-1", "member@example.com")).thenReturn(Optional.empty());

        WorkspaceMembershipService service = service(invitations, tenants, users);
        ResponseStatusException error = assertThrows(ResponseStatusException.class, () -> service.createInvitation(
                "tenant-1", "owner@example.com", "member@example.com", "PLATFORM_ADMIN", 7
        ));
        assertEquals(400, error.getStatusCode().value());
    }

    @Test
    void existingAccountAcceptsInvitationWithoutCreatingANewPassword() {
        WorkspaceInvitationRepository invitations = mock(WorkspaceInvitationRepository.class);
        TenantRepository tenants = mock(TenantRepository.class);
        UserRepository users = mock(UserRepository.class);
        RoleRepository roles = mock(RoleRepository.class);
        UserRoleRepository userRoles = mock(UserRoleRepository.class);

        UserEntity source = new UserEntity();
        source.setTenantId("source-tenant");
        source.setEmail("member@example.com");
        source.setDisplayName("Existing Member");
        source.setPasswordHash("existing-password-hash");
        source.setStatus("ACTIVE");

        WorkspaceInvitationEntity invitation = new WorkspaceInvitationEntity();
        invitation.setTenantId("tenant-1");
        invitation.setEmail("member@example.com");
        invitation.setRoleCode("USER");
        invitation.setStatus("PENDING");
        invitation.setExpiresAt(java.time.Instant.now().plusSeconds(3600));

        when(users.findByTenantIdAndEmail("source-tenant", "member@example.com")).thenReturn(Optional.of(source));
        when(users.findByTenantIdAndEmail("tenant-1", "member@example.com")).thenReturn(Optional.empty());
        when(invitations.findForUpdateByTokenHash(anyString())).thenReturn(Optional.of(invitation));
        when(tenants.findById("tenant-1")).thenReturn(Optional.of(activeTenant()));
        when(users.save(any(UserEntity.class))).thenAnswer(call -> {
            UserEntity value = call.getArgument(0);
            value.prePersist();
            return value;
        });
        when(roles.findByTenantIdAndCode("tenant-1", "USER")).thenReturn(Optional.of(new RoleEntity()));

        WorkspaceMembershipService service = new WorkspaceMembershipService(
                invitations, tenants, users, roles, userRoles, mock(PasswordEncoder.class)
        );
        WorkspaceMembershipService.AcceptanceResult result = service.acceptInvitationForExistingUser(
                "pwi_test.token", "source-tenant", "Member@Example.com"
        );

        ArgumentCaptor<UserEntity> captor = ArgumentCaptor.forClass(UserEntity.class);
        verify(users).save(captor.capture());
        assertEquals("existing-password-hash", captor.getValue().getPasswordHash());
        assertEquals("Existing Member", captor.getValue().getDisplayName());
        assertEquals("acme", result.tenantCode());
        assertEquals("ACCEPTED", invitation.getStatus());
    }

    private WorkspaceMembershipService service(
            WorkspaceInvitationRepository invitations,
            TenantRepository tenants,
            UserRepository users
    ) {
        return new WorkspaceMembershipService(
                invitations,
                tenants,
                users,
                mock(RoleRepository.class),
                mock(UserRoleRepository.class),
                mock(PasswordEncoder.class)
        );
    }

    private TenantEntity activeTenant() {
        TenantEntity tenant = new TenantEntity();
        tenant.setTenantId("tenant-1");
        tenant.setTenantCode("acme");
        tenant.setName("Acme");
        tenant.setStatus("ACTIVE");
        return tenant;
    }
}
