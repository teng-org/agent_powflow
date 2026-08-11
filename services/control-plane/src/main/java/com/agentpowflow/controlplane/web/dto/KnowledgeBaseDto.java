package com.agentpowflow.controlplane.web.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class KnowledgeBaseDto {
    private KnowledgeBaseDto() {}

    public record CreateRequest(
            @NotBlank @Size(max = 128) String name,
            @Size(max = 1000) String description,
            @Size(max = 10) List<@Size(max = 32) String> tags,
            String visibility
    ) {}
    public record RenameRequest(@NotBlank @Size(max = 128) String name) {}
    public record UpdateRequest(
            @NotBlank @Size(max = 128) String name,
            @Size(max = 1000) String description,
            @Size(max = 10) List<@Size(max = 32) String> tags,
            String visibility
    ) {}
    public record VisibilityRequest(@NotBlank String visibility) {}
    public record FreezeRequest(boolean frozen) {}
    public record BindingRequest(@NotBlank @Size(max = 64) String target_tenant_id) {}

    public record KnowledgeBaseView(
            String tenant_id,
            String kb_id,
            String name,
            String description,
            List<String> tags,
            String visibility,
            String summary,
            String status,
            boolean frozen,
            String metadata_owner,
            String owner_user_id,
            Instant created_at,
            Instant updated_at
    ) {}

    public record BindingView(
            String owner_tenant_id,
            String kb_id,
            String target_tenant_id,
            String created_by,
            Instant created_at,
            Instant updated_at
    ) {}

    public record ListResponse(String status, List<KnowledgeBaseView> items) {}
    public record ItemResponse(String status, KnowledgeBaseView item) {}
    public record BindingListResponse(String status, String kb_id, List<BindingView> items) {}
    public record BindingResponse(String status, BindingView item) {}
    public record DeleteResponse(String status, String deleted) {}
}
