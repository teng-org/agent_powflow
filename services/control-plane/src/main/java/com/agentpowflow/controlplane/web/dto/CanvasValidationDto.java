package com.agentpowflow.controlplane.web.dto;

import jakarta.validation.constraints.NotNull;

import java.util.List;
import java.util.Map;

public final class CanvasValidationDto {
    private CanvasValidationDto() {
    }

    public record ValidateRequest(
            String entry_node_id,
            @NotNull List<NodeDto> nodes,
            @NotNull List<EdgeDto> edges
    ) {
    }

    public record NodeDto(
            String id,
            String type,
            Map<String, Object> config
    ) {
    }

    public record EdgeDto(
            String id,
            String source_node_id,
            String target_node_id,
            String condition_label
    ) {
    }

    public record ValidateResponse(
            boolean valid,
            List<IssueDto> errors,
            List<IssueDto> warnings
    ) {
    }

    public record IssueDto(
            String node_id,
            String edge_id,
            String code,
            String message,
            String suggestion
    ) {
    }
}
