package com.agentpowflow.controlplane.web.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public final class DocumentMetadataDto {
    private DocumentMetadataDto() {}

    public record FreezeRequest(@NotNull Boolean frozen) {}

    public record ChunkDraftRequest(
            @NotBlank @Pattern(regexp = "^(edit|delete)$") String action,
            @Size(max = 1_000_000) String text
    ) {}
}
