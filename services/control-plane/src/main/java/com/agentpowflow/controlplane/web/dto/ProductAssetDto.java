package com.agentpowflow.controlplane.web.dto;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class ProductAssetDto {
    private ProductAssetDto() {}

    public record AgentUpsertRequest(
            @NotBlank @Size(max = 128) String name,
            @Size(max = 1000) String description,
            @Size(max = 30000) String instructions,
            JsonNode model_config,
            List<String> knowledge_base_ids,
            String workflow_id,
            String visibility,
            Long expected_revision
    ) {}

    public record AgentView(
            String agent_id,
            String tenant_id,
            String owner_user_id,
            String name,
            String description,
            String instructions,
            JsonNode model_config,
            List<String> knowledge_base_ids,
            String workflow_id,
            String visibility,
            String status,
            int latest_version_no,
            long revision,
            Instant created_at,
            Instant updated_at
    ) {}

    public record AgentVersionView(
            String version_id,
            String agent_id,
            int version_no,
            JsonNode snapshot,
            String checksum,
            String published_by,
            Instant published_at
    ) {}

    public record AgentResponse(String status, AgentView item) {}
    public record AgentListResponse(String status, List<AgentView> items) {}
    public record AgentVersionResponse(String status, AgentVersionView item) {}
    public record AgentVersionListResponse(String status, List<AgentVersionView> items) {}

    public record DeploymentCreateRequest(
            @NotBlank @Size(max = 128) String name,
            @NotBlank String asset_type,
            @NotBlank String asset_id,
            @Min(1) int asset_version_no,
            @Min(1) @Max(100000) Integer rate_limit_per_minute
    ) {}

    public record DeploymentStatusRequest(@NotBlank String status) {}

    public record DeploymentView(
            String deployment_id,
            String tenant_id,
            String owner_user_id,
            String name,
            String slug,
            String invoke_path,
            String asset_type,
            String asset_id,
            int asset_version_no,
            String status,
            int rate_limit_per_minute,
            Instant created_at,
            Instant updated_at
    ) {}

    public record DeploymentResponse(String status, DeploymentView item) {}
    public record DeploymentListResponse(String status, List<DeploymentView> items) {}

    public record ApiKeyCreateRequest(
            @NotBlank @Size(max = 128) String name,
            Instant expires_at
    ) {}

    public record ApiKeyView(
            String api_key_id,
            String deployment_id,
            String name,
            String key_prefix,
            String status,
            Instant expires_at,
            Instant last_used_at,
            Instant created_at,
            Instant revoked_at
    ) {}

    public record ApiKeyCreatedResponse(String status, ApiKeyView item, String secret) {}
    public record ApiKeyListResponse(String status, List<ApiKeyView> items) {}
    public record DeleteResponse(String status, String id, String disposition) {}

    public record InvocationView(
            String invocation_id,
            String deployment_id,
            String api_key_id,
            String tenant_id,
            String run_id,
            String status,
            long input_bytes,
            long output_bytes,
            Long duration_ms,
            String error_code,
            String trace_id,
            Instant created_at
    ) {}
    public record InvocationListResponse(String status, List<InvocationView> items) {}

    public record InvokeRequest(@NotNull JsonNode input, String conversation_id, Boolean stream) {}
    public record InvokeAcceptedResponse(
            String status,
            String deployment_id,
            String asset_type,
            String asset_id,
            int asset_version_no,
            String run_id,
            String trace_id,
            String status_url
    ) {}

    public record InvokeRunStatusResponse(
            String status,
            String deployment_id,
            String run_id,
            String stage,
            int progress,
            JsonNode output,
            String error_code,
            String error_message,
            String trace_id,
            Instant started_at,
            Instant finished_at
    ) {}

    public record AgentInvokeCompletedResponse(
            String status,
            String deployment_id,
            String asset_type,
            String asset_id,
            int asset_version_no,
            String run_id,
            String conversation_id,
            String trace_id,
            String output,
            JsonNode citations,
            JsonNode metrics,
            JsonNode memory
    ) {}
}
