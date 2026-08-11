package com.agentpowflow.controlplane.web.dto;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class WorkflowDto {
    private WorkflowDto() {
    }

    public record CreateRequest(
            @NotBlank @Size(max = 128) String name,
            @Size(max = 1000) String description,
            @NotNull JsonNode dsl,
            JsonNode layout
    ) {
    }

    public record SaveDraftRequest(
            @Size(max = 128) String name,
            @Size(max = 1000) String description,
            @NotNull JsonNode dsl,
            JsonNode layout,
            Long expected_revision
    ) {
    }

    public record PublishRequest(Long expected_revision) {
    }

    public record WorkflowView(
            String workflow_id,
            String tenant_id,
            String name,
            String description,
            String status,
            int latest_version_no,
            long revision,
            JsonNode draft_dsl,
            JsonNode draft_layout,
            String created_by,
            String updated_by,
            Instant created_at,
            Instant updated_at
    ) {
    }

    public record VersionView(
            String version_id,
            String workflow_id,
            String tenant_id,
            int version_no,
            JsonNode dsl,
            JsonNode layout,
            String checksum,
            String published_by,
            Instant published_at
    ) {
    }

    public record WorkflowResponse(String status, WorkflowView item, CanvasValidationDto.ValidateResponse validation) {
    }

    public record WorkflowListResponse(String status, List<WorkflowView> items) {
    }

    public record VersionResponse(String status, VersionView item) {
    }

    public record VersionListResponse(String status, List<VersionView> items) {
    }

    public record DeleteResponse(String status, String deleted_workflow_id) {
    }
}
