package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.product.AgentChatClient;
import com.agentpowflow.controlplane.product.DeploymentApiKeyEntity;
import com.agentpowflow.controlplane.product.DeploymentEntity;
import com.agentpowflow.controlplane.product.DeploymentInvocationEntity;
import com.agentpowflow.controlplane.product.ProductAssetService;
import com.agentpowflow.controlplane.runs.RunService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PublishedDeploymentControllerTest {
    private ProductAssetService productAssetService;
    private RunService runService;
    private AgentChatClient agentChatClient;
    private PublishedDeploymentController controller;

    @BeforeEach
    void setUp() {
        productAssetService = mock(ProductAssetService.class);
        runService = mock(RunService.class);
        agentChatClient = mock(AgentChatClient.class);
        controller = new PublishedDeploymentController(productAssetService, runService, agentChatClient);
    }

    @Test
    void agentInvocationCompletesAuditWithOutputBytes() {
        ProductAssetService.ResolvedInvocation resolved = resolved("AGENT");
        DeploymentInvocationEntity audit = audit("invocation-1", "trace-agent");
        when(productAssetService.resolveInvocation("agent-api", "pf_live_secret")).thenReturn(resolved);
        when(productAssetService.beginInvocation(eq(resolved), eq("trace-agent"), any(Long.class))).thenReturn(audit);
        JsonNode citations = JsonNodeFactory.instance.arrayNode().add(
                JsonNodeFactory.instance.objectNode().put("filename", "技术白皮书.pdf")
        );
        JsonNode metrics = JsonNodeFactory.instance.objectNode().put("total_ms", 1200.5);
        when(agentChatClient.invoke(any(), any(), any(), any(), any()))
                .thenReturn(new AgentChatClient.AgentResult(
                        "agent-run", "conversation-1", "调用完成", citations, metrics
                ));

        Object response = controller.invoke(
                "agent-api",
                new ProductAssetDto.InvokeRequest(JsonNodeFactory.instance.objectNode().put("message", "你好"), null, false),
                request("trace-agent")
        );

        ProductAssetDto.AgentInvokeCompletedResponse completed = (ProductAssetDto.AgentInvokeCompletedResponse) response;
        assertEquals("completed", completed.status());
        assertEquals("调用完成", completed.output());
        assertEquals("技术白皮书.pdf", completed.citations().path(0).path("filename").asText());
        assertEquals(1200.5, completed.metrics().path("total_ms").asDouble());
        verify(productAssetService).finishInvocation(
                eq("invocation-1"), eq("agent-run"), any(Long.class), eq("COMPLETED"),
                eq((long) "调用完成".getBytes(StandardCharsets.UTF_8).length), eq(null)
        );
    }

    @Test
    void workflowInvocationReturnsPublicStatusUrl() {
        ProductAssetService.ResolvedInvocation resolved = resolved("WORKFLOW");
        DeploymentInvocationEntity audit = audit("invocation-2", "trace-workflow");
        RunDto.RunView run = run("workflow-run", "PENDING", JsonNodeFactory.instance.objectNode());
        when(productAssetService.resolveInvocation("workflow-api", "pf_live_secret")).thenReturn(resolved);
        when(productAssetService.beginInvocation(eq(resolved), eq("trace-workflow"), any(Long.class))).thenReturn(audit);
        when(runService.createPublishedApiRun(any(), any(), any(), any(), eq(3)))
                .thenReturn(new RunDto.MessageRunResponse("ok", null, run));

        ProductAssetDto.InvokeAcceptedResponse response = (ProductAssetDto.InvokeAcceptedResponse) controller.invoke(
                "workflow-api",
                new ProductAssetDto.InvokeRequest(JsonNodeFactory.instance.objectNode().put("message", "运行"), null, false),
                request("trace-workflow")
        );

        assertEquals("accepted", response.status());
        assertEquals("/api/v1/deployments/workflow-api/invoke/workflow-run", response.status_url());
        verify(productAssetService).finishInvocation(
                eq("invocation-2"), eq("workflow-run"), any(Long.class), eq("ACCEPTED"), eq(0L), eq(null)
        );
    }

    @Test
    void publicRunStatusIsBoundToDeploymentKeyAndSynchronizesAudit() {
        ProductAssetService.ResolvedInvocation resolved = resolved("WORKFLOW");
        DeploymentInvocationEntity invocation = audit("invocation-3", "trace-status");
        JsonNode output = JsonNodeFactory.instance.objectNode().put("answer", "完成");
        RunDto.RunView run = run("workflow-run", "SUCCEEDED", output);
        when(productAssetService.resolveInvocation("workflow-api", "pf_live_secret")).thenReturn(resolved);
        when(productAssetService.requireRunInvocation(resolved, "workflow-run")).thenReturn(invocation);
        when(runService.internalGetRun("tenant-1", "workflow-run")).thenReturn(run);

        ProductAssetDto.InvokeRunStatusResponse response = controller.runStatus(
                "workflow-api", "workflow-run", request("trace-status")
        );

        assertEquals("succeeded", response.status());
        assertEquals(output, response.output());
        verify(productAssetService).syncRunInvocation(
                invocation, "SUCCEEDED", output, "", run.started_at(), run.finished_at()
        );
    }

    @Test
    void streamingRequestsAreRejectedInsteadOfSilentlyIgnored() {
        ResponseStatusException error = assertThrows(ResponseStatusException.class, () -> controller.invoke(
                "agent-api", new ProductAssetDto.InvokeRequest(JsonNodeFactory.instance.objectNode(), null, true), request("trace")
        ));
        assertEquals(422, error.getStatusCode().value());
    }

    private MockHttpServletRequest request(String traceId) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer pf_live_secret");
        request.addHeader("X-Trace-Id", traceId);
        return request;
    }

    private ProductAssetService.ResolvedInvocation resolved(String type) {
        DeploymentEntity deployment = new DeploymentEntity();
        deployment.setTenantId("tenant-1");
        deployment.setOwnerUserId("owner-1");
        deployment.setName("测试发布");
        deployment.setSlug(type.equals("AGENT") ? "agent-api" : "workflow-api");
        deployment.setAssetType(type);
        deployment.setAssetId(type.equals("AGENT") ? "agent-1" : "workflow-1");
        deployment.setAssetVersionNo(3);
        DeploymentApiKeyEntity key = new DeploymentApiKeyEntity();
        return new ProductAssetService.ResolvedInvocation(
                deployment, key, "workflow-1", 3, JsonNodeFactory.instance.objectNode()
        );
    }

    private DeploymentInvocationEntity audit(String invocationId, String traceId) {
        DeploymentInvocationEntity invocation = new DeploymentInvocationEntity();
        try {
            var field = DeploymentInvocationEntity.class.getDeclaredField("invocationId");
            field.setAccessible(true);
            field.set(invocation, invocationId);
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException(error);
        }
        invocation.setStatus("ACCEPTING");
        invocation.setTraceId(traceId);
        return invocation;
    }

    private RunDto.RunView run(String runId, String status, JsonNode output) {
        Instant now = Instant.now();
        return new RunDto.RunView(
                runId, "tenant-1", "conversation-1", "workflow-1", "version-3", 3, "checksum",
                status, status.equals("SUCCEEDED") ? "DONE" : "QUEUED", status.equals("SUCCEEDED") ? 100 : 0,
                JsonNodeFactory.instance.objectNode(), output, JsonNodeFactory.instance.arrayNode(),
                JsonNodeFactory.instance.objectNode(), "", "", "owner-1", now, now, now, now, 1
        );
    }
}
