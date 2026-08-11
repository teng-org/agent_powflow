package com.agentpowflow.controlplane.knowledge;

import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.web.dto.KnowledgeBaseDto;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class KnowledgeBaseServiceTest {
    private KnowledgeBaseRepository repository;
    private KnowledgeBaseBindingRepository bindingRepository;
    private TenantRepository tenantRepository;
    private KnowledgeExecutionClient executionClient;
    private KnowledgeBaseService service;

    @BeforeEach
    void setUp() {
        repository = mock(KnowledgeBaseRepository.class);
        bindingRepository = mock(KnowledgeBaseBindingRepository.class);
        tenantRepository = mock(TenantRepository.class);
        executionClient = mock(KnowledgeExecutionClient.class);
        service = new KnowledgeBaseService(repository, bindingRepository, tenantRepository, executionClient);
        when(repository.saveAndFlush(any(KnowledgeBaseEntity.class))).thenAnswer(invocation -> {
            KnowledgeBaseEntity entity = invocation.getArgument(0);
            if (entity.getCreatedAt() == null) entity.prePersist();
            else entity.preUpdate();
            return entity;
        });
        when(bindingRepository.saveAndFlush(any(KnowledgeBaseBindingEntity.class))).thenAnswer(invocation -> {
            KnowledgeBaseBindingEntity entity = invocation.getArgument(0);
            if (entity.getCreatedAt() == null) entity.prePersist();
            else entity.preUpdate();
            return entity;
        });
    }

    @Test
    void createSerializesTenantNamesAndMarksControlAuthority() {
        when(repository.findByTenantIdOrderByCreatedAtAsc("tenant-a"))
                .thenReturn(List.of(kb("tenant-a", "kb-existing", "Library")));

        KnowledgeBaseDto.KnowledgeBaseView created = service.create("tenant-a", "Library");

        verify(repository).lockTenant("tenant-a");
        assertEquals("Library (2)", created.name());
        assertEquals("CONTROL", created.metadata_owner());
        assertEquals("PRIVATE", created.visibility());
        assertNotEquals("kb-existing", created.kb_id());
    }

    @Test
    void tenantScopedMutationNeverFallsBackAcrossTenants() {
        when(repository.findByTenantIdAndKbId("tenant-b", "kb-a")).thenReturn(Optional.empty());

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.rename("tenant-b", "kb-a", "Hijacked")
        );

        assertEquals(HttpStatus.NOT_FOUND, error.getStatusCode());
    }

    @Test
    void visibilityIsFailClosed() {
        when(repository.findByTenantIdAndKbId("tenant-a", "kb-a"))
                .thenReturn(Optional.of(kb("tenant-a", "kb-a", "Library")));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.setVisibility("tenant-a", "kb-a", "PUBLIC")
        );

        assertEquals(HttpStatus.BAD_REQUEST, error.getStatusCode());
    }

    @Test
    void bindingRequiresExistingTargetTenant() {
        when(repository.findByTenantIdAndKbId("tenant-a", "kb-a"))
                .thenReturn(Optional.of(kb("tenant-a", "kb-a", "Library")));
        when(tenantRepository.existsById("tenant-missing")).thenReturn(false);

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.bind("tenant-a", "actor-a", "kb-a", "tenant-missing")
        );

        assertEquals(HttpStatus.NOT_FOUND, error.getStatusCode());
    }

    @Test
    void deleteDelegatesPhysicalCleanupOnlyAfterTenantScopedLookup() {
        when(repository.findByTenantIdAndKbId("tenant-a", "kb-a"))
                .thenReturn(Optional.of(kb("tenant-a", "kb-a", "Library")));

        service.delete("tenant-a", "kb-a");

        verify(executionClient).deleteKnowledgeBase("tenant-a", "kb-a");
    }

    private KnowledgeBaseEntity kb(String tenantId, String kbId, String name) {
        KnowledgeBaseEntity entity = new KnowledgeBaseEntity();
        entity.setTenantId(tenantId);
        entity.setKbId(kbId);
        entity.setName(name);
        entity.prePersist();
        return entity;
    }
}
