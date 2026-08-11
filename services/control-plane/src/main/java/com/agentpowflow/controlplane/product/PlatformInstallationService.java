package com.agentpowflow.controlplane.product;

import com.agentpowflow.controlplane.knowledge.KnowledgeExecutionClient;
import com.agentpowflow.controlplane.web.dto.PlatformSetupDto;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Service
public class PlatformInstallationService {
    private static final String PRIMARY = "primary";

    private final PlatformInstallationRepository repository;
    private final KnowledgeExecutionClient executionClient;
    private final JdbcTemplate jdbcTemplate;

    public PlatformInstallationService(
            PlatformInstallationRepository repository,
            KnowledgeExecutionClient executionClient,
            JdbcTemplate jdbcTemplate
    ) {
        this.repository = repository;
        this.executionClient = executionClient;
        this.jdbcTemplate = jdbcTemplate;
    }

    @Transactional
    public PlatformSetupDto.SetupView status(boolean refreshChecks) {
        PlatformInstallationEntity entity = repository.findById(PRIMARY)
                .orElseThrow(() -> new IllegalStateException("platform installation row missing"));
        Map<String, Object> execution = refreshChecks ? executionClient.platformReadiness() : Map.of();
        boolean databaseReady = databaseReady();
        boolean executionReady = "ok".equals(String.valueOf(execution.getOrDefault("status", "unknown")));
        boolean providerReady = executionReady && asBoolean(execution.get("provider_ready"));
        boolean storageReady = databaseReady && executionReady && asBoolean(execution.get("storage_ready"));
        if (refreshChecks) {
            entity.setProviderReady(providerReady);
            entity.setStorageReady(storageReady);
            if (!"READY".equals(entity.getState())) entity.setState("IN_PROGRESS");
            entity = repository.saveAndFlush(entity);
        }
        return view(entity, databaseReady, executionReady, execution);
    }

    @Transactional
    public PlatformSetupDto.SetupView saveSite(PlatformSetupDto.SiteRequest request) {
        PlatformInstallationEntity entity = repository.findById(PRIMARY)
                .orElseThrow(() -> new IllegalStateException("platform installation row missing"));
        entity.setSiteName(request.site_name().trim());
        entity.setPublicConsoleUrl(clean(request.public_console_url()));
        entity.setPublicApiUrl(clean(request.public_api_url()));
        if (!"READY".equals(entity.getState())) entity.setState("IN_PROGRESS");
        repository.saveAndFlush(entity);
        return status(true);
    }

    @Transactional
    public PlatformSetupDto.SetupView complete(String actorId) {
        PlatformSetupDto.SetupView checks = status(true);
        if (!checks.database_ready() || !checks.storage_ready() || !checks.provider_ready()) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "database, execution/storage and both LLM + embedding providers must be ready"
            );
        }
        PlatformInstallationEntity entity = repository.findById(PRIMARY).orElseThrow();
        entity.setState("READY");
        entity.setCompletedBy(actorId);
        entity.setCompletedAt(Instant.now());
        repository.saveAndFlush(entity);
        return status(false);
    }

    private PlatformSetupDto.SetupView view(
            PlatformInstallationEntity entity,
            boolean databaseReady,
            boolean executionReady,
            Map<String, Object> execution
    ) {
        List<String> missing = new ArrayList<>();
        Object rawMissing = execution.get("missing_capabilities");
        if (rawMissing instanceof List<?> values) values.forEach(value -> missing.add(String.valueOf(value)));
        return new PlatformSetupDto.SetupView(
                entity.getState(), entity.getSiteName(), entity.getPublicConsoleUrl(), entity.getPublicApiUrl(),
                databaseReady, executionReady, entity.isStorageReady(), entity.isProviderReady(),
                asInt(execution.get("enabled_endpoint_count")), asInt(execution.get("llm_endpoint_count")),
                asInt(execution.get("embedding_endpoint_count")), missing, entity.getCompletedAt(), entity.getUpdatedAt()
        );
    }

    private boolean databaseReady() {
        try { return Integer.valueOf(1).equals(jdbcTemplate.queryForObject("SELECT 1", Integer.class)); }
        catch (RuntimeException exception) { return false; }
    }

    private boolean asBoolean(Object value) { return value instanceof Boolean bool && bool; }
    private int asInt(Object value) { return value instanceof Number number ? number.intValue() : 0; }
    private String clean(String value) { return value == null ? "" : value.trim(); }
}
