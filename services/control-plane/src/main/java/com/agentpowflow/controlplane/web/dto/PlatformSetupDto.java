package com.agentpowflow.controlplane.web.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class PlatformSetupDto {
    private PlatformSetupDto() {}

    public record SiteRequest(
            @NotBlank @Size(max = 128) String site_name,
            @Size(max = 512) String public_console_url,
            @Size(max = 512) String public_api_url
    ) {}

    public record SetupView(
            String state,
            String site_name,
            String public_console_url,
            String public_api_url,
            boolean database_ready,
            boolean execution_ready,
            boolean storage_ready,
            boolean provider_ready,
            int enabled_endpoint_count,
            int llm_endpoint_count,
            int embedding_endpoint_count,
            List<String> missing_capabilities,
            Instant completed_at,
            Instant updated_at
    ) {}

    public record SetupResponse(String status, SetupView item) {}
}
