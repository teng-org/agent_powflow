package com.agentpowflow.controlplane.orchestration;

import com.agentpowflow.controlplane.canvas.CanvasDslMapper;
import com.agentpowflow.controlplane.canvas.ConnectionRuleEngine;
import com.agentpowflow.controlplane.canvas.DslValidator;
import com.agentpowflow.controlplane.canvas.NodeTypeRegistry;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkflowServiceTest {
    private final ObjectMapper objectMapper = new ObjectMapper();
    private WorkflowRepository workflowRepository;
    private WorkflowVersionRepository versionRepository;
    private WorkflowService service;

    @BeforeEach
    void setUp() {
        workflowRepository = mock(WorkflowRepository.class);
        versionRepository = mock(WorkflowVersionRepository.class);
        NodeTypeRegistry registry = new NodeTypeRegistry();
        DslValidator validator = new DslValidator(registry, new ConnectionRuleEngine(registry));
        service = new WorkflowService(
                workflowRepository,
                versionRepository,
                objectMapper,
                new CanvasDslMapper(objectMapper),
                validator
        );
        when(workflowRepository.saveAndFlush(any(WorkflowEntity.class))).thenAnswer(invocation -> {
            WorkflowEntity entity = invocation.getArgument(0);
            if (entity.getCreatedAt() == null) entity.prePersist();
            else entity.preUpdate();
            return entity;
        });
        when(versionRepository.save(any(WorkflowVersionEntity.class))).thenAnswer(invocation -> {
            WorkflowVersionEntity entity = invocation.getArgument(0);
            entity.prePersist();
            return entity;
        });
    }

    @Test
    void publishesValidatedImmutableVersion() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", validDsl());
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        WorkflowDto.VersionView published = service.publish("tenant-a", "user-a", "wf-1", 0L);

        assertEquals(1, published.version_no());
        assertEquals("tenant-a", published.tenant_id());
        assertEquals(64, published.checksum().length());
        assertNotNull(published.version_id());
        assertEquals("PUBLISHED", workflow.getStatus());
        assertEquals(1, workflow.getLatestVersionNo());
    }

    @Test
    void rejectsInvalidDraftAtPublishBoundary() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", objectMapper.readTree("""
                {"entry_node_id":"start-1","nodes":[{"id":"start-1","type":"start","config":{}}],"edges":[]}
                """));
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.publish("tenant-a", "user-a", "wf-1", 0L)
        );

        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, error.getStatusCode());
        verify(versionRepository, never()).save(any());
    }

    @Test
    void tenantScopedLookupDoesNotFallBackAcrossTenants() {
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-b")).thenReturn(Optional.empty());

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.get("tenant-b", "wf-1")
        );

        assertEquals(HttpStatus.NOT_FOUND, error.getStatusCode());
    }

    @Test
    void staleRevisionCannotOverwriteDraft() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", validDsl());
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.saveDraft(
                        "tenant-a", "user-a", "wf-1", null, null, validDsl(), objectMapper.createObjectNode(), 9L
                )
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
    }

    @Test
    void saveDraftFlushesBeforeReturningRevision() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", validDsl());
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        service.saveDraft(
                "tenant-a", "user-a", "wf-1", "Updated", null,
                validDsl(), objectMapper.createObjectNode(), 0L
        );

        verify(workflowRepository).saveAndFlush(workflow);
    }

    @Test
    void draftCanRemainInvalidUntilExplicitPublish() throws Exception {
        JsonNode invalid = objectMapper.readTree("""
                {"entry_node_id":null,"nodes":[],"edges":[]}
                """);

        WorkflowDto.WorkflowView created = service.create("tenant-a", "user-a", "Draft", "", invalid, null);

        assertEquals("DRAFT", created.status());
        assertTrue(created.draft_layout().isObject());
        verify(workflowRepository).saveAndFlush(any(WorkflowEntity.class));
    }

    @Test
    void deletionRequiresArchivedState() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", validDsl());
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> service.deleteArchived("tenant-a", "wf-1")
        );

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        verify(workflowRepository, never()).delete(any());
    }

    @Test
    void unpublishedDraftCanBeDeletedDirectly() throws Exception {
        WorkflowEntity workflow = workflow("tenant-a", validDsl());
        when(workflowRepository.findByWorkflowIdAndTenantId("wf-1", "tenant-a")).thenReturn(Optional.of(workflow));

        service.deleteDraft("tenant-a", "wf-1");

        verify(workflowRepository).delete(workflow);
    }

    private WorkflowEntity workflow(String tenantId, JsonNode dsl) throws Exception {
        WorkflowEntity workflow = new WorkflowEntity();
        workflow.setWorkflowId("wf-1");
        workflow.setTenantId(tenantId);
        workflow.setName("Workflow");
        workflow.setDescription("");
        workflow.setDraftDslJson(objectMapper.writeValueAsString(dsl));
        workflow.setDraftLayoutJson("{}");
        workflow.setCreatedBy("user-a");
        workflow.setUpdatedBy("user-a");
        workflow.prePersist();
        return workflow;
    }

    private JsonNode validDsl() throws Exception {
        return objectMapper.readTree("""
                {
                  "entry_node_id":"start-1",
                  "nodes":[
                    {"id":"start-1","type":"start","config":{}},
                    {"id":"llm-1","type":"llm.generate","config":{}},
                    {"id":"end-1","type":"end","config":{}}
                  ],
                  "edges":[
                    {"id":"e1","source_node_id":"start-1","target_node_id":"llm-1","condition_label":""},
                    {"id":"e2","source_node_id":"llm-1","target_node_id":"end-1","condition_label":""}
                  ]
                }
                """);
    }
}
