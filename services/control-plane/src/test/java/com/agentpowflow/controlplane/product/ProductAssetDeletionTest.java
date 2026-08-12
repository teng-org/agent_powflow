package com.agentpowflow.controlplane.product;

import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.integrations.IntegrationService;
import com.agentpowflow.controlplane.integrations.JavaScriptToolService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ProductAssetDeletionTest {
    private AgentRepository agents;
    private AgentVersionRepository agentVersions;
    private DeploymentRepository deployments;
    private DeploymentApiKeyRepository apiKeys;
    private WorkflowService workflows;
    private ProductAssetService service;

    @BeforeEach
    void setUp() {
        agents = mock(AgentRepository.class);
        agentVersions = mock(AgentVersionRepository.class);
        deployments = mock(DeploymentRepository.class);
        apiKeys = mock(DeploymentApiKeyRepository.class);
        workflows = mock(WorkflowService.class);
        service = new ProductAssetService(
                agents,
                agentVersions,
                deployments,
                apiKeys,
                mock(DeploymentInvocationRepository.class),
                workflows,
                mock(IntegrationService.class),
                mock(JavaScriptToolService.class),
                new ObjectMapper(),
                mock(PasswordEncoder.class)
        );
    }

    @Test
    void activeDeploymentBlocksAgentDeletion() {
        AgentEntity agent = agent("agent-1", "DRAFT", 0);
        when(agents.findByAgentIdAndTenantId("agent-1", "tenant-1")).thenReturn(Optional.of(agent));
        when(deployments.countByTenantIdAndAssetTypeAndAssetIdAndStatusNot(
                "tenant-1", "AGENT", "agent-1", "ARCHIVED"
        )).thenReturn(1L);

        AssetInUseException error = assertThrows(
                AssetInUseException.class,
                () -> service.deleteAgent("tenant-1", "user-1", "agent-1")
        );

        assertEquals("该智能体仍被 API 发布使用，请先删除对应的 API 发布。", error.getMessage());
        verify(agents, never()).delete(any());
    }

    @Test
    void publishedAgentIsArchivedAndDraftIsDeleted() {
        AgentEntity published = agent("agent-published", "PUBLISHED", 2);
        when(agents.findByAgentIdAndTenantId("agent-published", "tenant-1")).thenReturn(Optional.of(published));
        ProductAssetDto.DeleteResponse archived = service.deleteAgent(
                "tenant-1", "user-1", "agent-published"
        );

        AgentEntity draft = agent("agent-draft", "DRAFT", 0);
        when(agents.findByAgentIdAndTenantId("agent-draft", "tenant-1")).thenReturn(Optional.of(draft));
        when(agentVersions.findByTenantIdAndAgentIdOrderByVersionNoDesc("tenant-1", "agent-draft"))
                .thenReturn(List.of());
        ProductAssetDto.DeleteResponse deleted = service.deleteAgent("tenant-1", "user-1", "agent-draft");

        assertEquals("ARCHIVED", archived.disposition());
        assertEquals("ARCHIVED", published.getStatus());
        assertEquals("DELETED", deleted.disposition());
        verify(agents).saveAndFlush(published);
        verify(agents).delete(draft);
    }

    @Test
    void linkedAgentBlocksWorkflowDeletion() {
        when(workflows.get("tenant-1", "workflow-1")).thenReturn(workflow("workflow-1", "DRAFT", 0));
        when(agents.countByTenantIdAndWorkflowIdAndStatusNot("tenant-1", "workflow-1", "ARCHIVED"))
                .thenReturn(1L);

        assertThrows(
                AssetInUseException.class,
                () -> service.deleteWorkflow("tenant-1", "user-1", "workflow-1")
        );
        verify(workflows, never()).deleteDraft(any(), any());
    }

    @Test
    void deploymentDeletionArchivesServiceAndRevokesKeys() {
        DeploymentEntity deployment = new DeploymentEntity();
        deployment.setTenantId("tenant-1");
        deployment.setOwnerUserId("user-1");
        deployment.setName("API");
        deployment.setSlug("api");
        deployment.setAssetType("AGENT");
        deployment.setAssetId("agent-1");
        deployment.setAssetVersionNo(1);
        deployment.prePersist();
        DeploymentApiKeyEntity active = apiKey("ACTIVE");
        DeploymentApiKeyEntity revoked = apiKey("REVOKED");
        when(deployments.findByDeploymentIdAndTenantId(deployment.getDeploymentId(), "tenant-1"))
                .thenReturn(Optional.of(deployment));
        when(apiKeys.findByTenantIdAndDeploymentIdOrderByCreatedAtDesc("tenant-1", deployment.getDeploymentId()))
                .thenReturn(List.of(active, revoked));

        ProductAssetDto.DeleteResponse result = service.deleteDeployment(
                "tenant-1", "user-1", deployment.getDeploymentId()
        );

        assertEquals("ARCHIVED", result.disposition());
        assertEquals("ARCHIVED", deployment.getStatus());
        assertEquals("REVOKED", active.getStatus());
        verify(apiKeys).saveAll(List.of(active, revoked));
        verify(deployments).saveAndFlush(deployment);
    }

    @Test
    void agentPersistsNormalizedRag2RetrievalConfiguration() {
        ObjectNode modelConfig = new ObjectMapper().createObjectNode();
        ObjectNode retrieval = modelConfig.putObject("retrieval");
        retrieval.put("enabled", true);
        retrieval.put("rank_method", "keyword");
        retrieval.put("top_k", 5);
        retrieval.put("rerank_enabled", true);
        retrieval.put("rerank_k", 24);
        retrieval.put("rerank_required", true);
        when(agents.saveAndFlush(any())).thenAnswer(invocation -> {
            AgentEntity entity = invocation.getArgument(0);
            entity.prePersist();
            return entity;
        });

        ProductAssetDto.AgentView result = service.createAgent(
                "tenant-1",
                "user-1",
                new ProductAssetDto.AgentUpsertRequest(
                        "RAG Agent", "", "", modelConfig, List.of(), null, "PRIVATE", null
                )
        );

        assertEquals("keyword", result.model_config().path("retrieval").path("rank_method").asText());
        assertEquals(5, result.model_config().path("retrieval").path("top_k").asInt());
        assertEquals(true, result.model_config().path("retrieval").path("rerank_enabled").asBoolean());
        assertEquals(24, result.model_config().path("retrieval").path("rerank_k").asInt());
        assertEquals(true, result.model_config().path("retrieval").path("rerank_required").asBoolean());
    }

    private AgentEntity agent(String id, String status, int latestVersion) {
        AgentEntity entity = new AgentEntity();
        entity.setAgentId(id);
        entity.setTenantId("tenant-1");
        entity.setOwnerUserId("user-1");
        entity.setName("Agent");
        entity.setStatus(status);
        entity.setLatestVersionNo(latestVersion);
        entity.prePersist();
        return entity;
    }

    private WorkflowDto.WorkflowView workflow(String id, String status, int latestVersion) {
        return new WorkflowDto.WorkflowView(
                id, "tenant-1", "Workflow", "", status, latestVersion, 0,
                new ObjectMapper().createObjectNode(), new ObjectMapper().createObjectNode(),
                "user-1", "user-1", Instant.now(), Instant.now()
        );
    }

    private DeploymentApiKeyEntity apiKey(String status) {
        DeploymentApiKeyEntity key = new DeploymentApiKeyEntity();
        key.setDeploymentId("deployment-1");
        key.setTenantId("tenant-1");
        key.setOwnerUserId("user-1");
        key.setName("Key");
        key.setKeyPrefix("prefix-" + status);
        key.setSecretHash("hash");
        key.setStatus(status);
        key.prePersist();
        return key;
    }
}
