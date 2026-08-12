package com.agentpowflow.controlplane.product;

import com.agentpowflow.controlplane.orchestration.WorkflowService;
import com.agentpowflow.controlplane.integrations.IntegrationService;
import com.agentpowflow.controlplane.integrations.JavaScriptToolService;
import com.agentpowflow.controlplane.web.dto.ProductAssetDto;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

@Service
public class ProductAssetService {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String KEY_PREFIX = "pf_live_";
    private static final int KEY_LOOKUP_LENGTH = 20;

    private final AgentRepository agentRepository;
    private final AgentVersionRepository versionRepository;
    private final DeploymentRepository deploymentRepository;
    private final DeploymentApiKeyRepository apiKeyRepository;
    private final DeploymentInvocationRepository invocationRepository;
    private final WorkflowService workflowService;
    private final IntegrationService integrationService;
    private final JavaScriptToolService javaScriptToolService;
    private final ObjectMapper objectMapper;
    private final PasswordEncoder passwordEncoder;

    public ProductAssetService(
            AgentRepository agentRepository,
            AgentVersionRepository versionRepository,
            DeploymentRepository deploymentRepository,
            DeploymentApiKeyRepository apiKeyRepository,
            DeploymentInvocationRepository invocationRepository,
            WorkflowService workflowService,
            IntegrationService integrationService,
            JavaScriptToolService javaScriptToolService,
            ObjectMapper objectMapper,
            PasswordEncoder passwordEncoder
    ) {
        this.agentRepository = agentRepository;
        this.versionRepository = versionRepository;
        this.deploymentRepository = deploymentRepository;
        this.apiKeyRepository = apiKeyRepository;
        this.invocationRepository = invocationRepository;
        this.workflowService = workflowService;
        this.integrationService = integrationService;
        this.javaScriptToolService = javaScriptToolService;
        this.objectMapper = objectMapper;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.AgentView> listAgents(String tenantId, String ownerUserId) {
        return agentRepository.findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(tenantId, ownerUserId)
                .stream().filter(item -> !"ARCHIVED".equals(item.getStatus()))
                .map(this::agentView).toList();
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.AgentView> listPlatformAgents() {
        return agentRepository.findAll().stream()
                .sorted((left, right) -> right.getUpdatedAt().compareTo(left.getUpdatedAt()))
                .map(this::agentView).toList();
    }

    @Transactional
    public ProductAssetDto.AgentView createAgent(
            String tenantId, String ownerUserId, ProductAssetDto.AgentUpsertRequest request
    ) {
        AgentEntity entity = new AgentEntity();
        entity.setTenantId(required(tenantId, "tenant id missing"));
        entity.setOwnerUserId(required(ownerUserId, "owner user id missing"));
        applyAgent(entity, request);
        return agentView(agentRepository.saveAndFlush(entity));
    }

    @Transactional(readOnly = true)
    public ProductAssetDto.AgentView getAgent(String tenantId, String ownerUserId, String agentId) {
        return agentView(requireOwnedAgent(tenantId, ownerUserId, agentId));
    }

    @Transactional
    public ProductAssetDto.AgentView updateAgent(
            String tenantId, String ownerUserId, String agentId, ProductAssetDto.AgentUpsertRequest request
    ) {
        AgentEntity entity = requireOwnedAgent(tenantId, ownerUserId, agentId);
        if ("ARCHIVED".equals(entity.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "archived agent is read-only");
        }
        if (request.expected_revision() != null && request.expected_revision() != entity.getRowVersion()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "agent revision conflict");
        }
        applyAgent(entity, request);
        return agentView(agentRepository.saveAndFlush(entity));
    }

    @Transactional
    public ProductAssetDto.AgentVersionView publishAgent(String tenantId, String ownerUserId, String agentId) {
        AgentEntity entity = requireOwnedAgent(tenantId, ownerUserId, agentId);
        if ("ARCHIVED".equals(entity.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "archived agent cannot be published");
        }
        int workflowVersionNo = 0;
        if (entity.getWorkflowId() != null && !entity.getWorkflowId().isBlank()) {
            WorkflowDto.WorkflowView workflow = workflowService.get(tenantId, entity.getWorkflowId());
            if (!ownerUserId.equals(workflow.created_by())) {
                throw new ResponseStatusException(HttpStatus.FORBIDDEN, "agent workflow is not owned by current user");
            }
            workflowVersionNo = workflow.latest_version_no();
            if (workflowVersionNo < 1) {
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "agent workflow has no published version");
            }
            workflowService.getVersion(tenantId, entity.getWorkflowId(), workflowVersionNo);
        }

        ObjectNode snapshot = objectMapper.createObjectNode();
        snapshot.put("agent_id", entity.getAgentId());
        snapshot.put("name", entity.getName());
        snapshot.put("description", entity.getDescription());
        snapshot.put("instructions", entity.getInstructions());
        ObjectNode publishedModelConfig = (ObjectNode) readJson(entity.getModelConfigJson());
        ArrayNode publishedSkills = objectMapper.createArrayNode();
        for (JsonNode selected : publishedModelConfig.path("skills")) {
            String skillId = clean(selected.path("skill_id").asText(""));
            if (skillId.isBlank()) continue;
            IntegrationService.SkillView skill = integrationService.resolveOwnedSkill(tenantId, ownerUserId, skillId);
            int versionNo = selected.path("version").asInt(skill.latest_version_no());
            if (versionNo < 1) {
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "selected skill has no published version");
            }
            JsonNode definition = integrationService.resolveSkill(tenantId, skillId, versionNo);
            ObjectNode pinned = objectMapper.createObjectNode();
            pinned.put("skill_id", skillId);
            pinned.put("name", skill.name());
            pinned.put("version", versionNo);
            pinned.put("instructions", clean(definition.path("instructions").asText("")));
            pinned.put("description", skill.description());
            pinned.set("input_schema", definition.path("input_schema").isObject() ? definition.path("input_schema") : objectMapper.createObjectNode().put("type", "object"));
            pinned.set("output_schema", definition.path("output_schema").isObject() ? definition.path("output_schema") : objectMapper.createObjectNode().put("type", "object"));
            pinned.put("max_tokens", boundedInt(definition.path("max_tokens").asInt(800), 64, 8192, "skill.max_tokens"));
            publishedSkills.add(pinned);
        }
        publishedModelConfig.set("skills", publishedSkills);
        ArrayNode publishedMcpTools = objectMapper.createArrayNode();
        for (JsonNode selected : publishedModelConfig.path("mcp_tools")) {
            String connectionId = clean(selected.path("connection_id").asText(""));
            String toolName = clean(selected.path("tool_name").asText(""));
            if (connectionId.isBlank() || toolName.isBlank()) continue;
            IntegrationService.McpView connection = integrationService.resolveOwnedMcp(tenantId, ownerUserId, connectionId);
            JsonNode tool = null;
            for (JsonNode candidate : connection.tools()) {
                if (toolName.equals(candidate.path("name").asText())) { tool = candidate; break; }
            }
            if (tool == null) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "selected MCP tool is unavailable");
            ObjectNode pinned = objectMapper.createObjectNode();
            pinned.put("connection_id", connectionId);
            pinned.put("connection_name", connection.name());
            pinned.put("endpoint_url", connection.endpoint_url());
            ObjectNode auth = objectMapper.createObjectNode();
            auth.put("type", connection.auth_type());
            auth.put("secret_ref", connection.secret_ref() == null ? "" : connection.secret_ref());
            pinned.set("auth", auth);
            pinned.put("tool_name", toolName);
            pinned.put("title", clean(tool.path("title").asText(toolName)));
            pinned.put("description", clean(tool.path("description").asText("")));
            pinned.set("input_schema", tool.path("inputSchema").isObject() ? tool.path("inputSchema") : objectMapper.createObjectNode().put("type", "object"));
            pinned.set("annotations", tool.path("annotations").isObject() ? tool.path("annotations") : objectMapper.createObjectNode());
            publishedMcpTools.add(pinned);
        }
        publishedModelConfig.set("mcp_tools", publishedMcpTools);
        ArrayNode publishedJavaScriptTools = objectMapper.createArrayNode();
        for (JsonNode selected : publishedModelConfig.path("javascript_tools")) {
            String toolId = clean(selected.path("tool_id").asText(""));
            if (toolId.isBlank()) continue;
            JavaScriptToolService.ToolView tool = javaScriptToolService.resolveOwned(tenantId, ownerUserId, toolId);
            int versionNo = selected.path("version").asInt(tool.latest_version_no());
            if (versionNo < 1) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "selected JavaScript tool has no published version");
            JsonNode definition = javaScriptToolService.resolveVersion(tenantId, toolId, versionNo);
            if (!definition.path("allow_agent").asBoolean()) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "selected JavaScript tool is not assigned to AI");
            ObjectNode pinned = objectMapper.createObjectNode();
            pinned.put("tool_id", toolId); pinned.put("name", tool.name()); pinned.put("description", tool.description()); pinned.put("version", versionNo);
            pinned.set("definition", definition); publishedJavaScriptTools.add(pinned);
        }
        publishedModelConfig.set("javascript_tools", publishedJavaScriptTools);
        snapshot.set("model_config", publishedModelConfig);
        snapshot.set("knowledge_base_ids", readJson(entity.getKnowledgeBaseIdsJson()));
        if (entity.getWorkflowId() != null && !entity.getWorkflowId().isBlank()) {
            snapshot.put("workflow_id", entity.getWorkflowId());
            snapshot.put("workflow_version_no", workflowVersionNo);
        }

        String canonical = writeJson(snapshot);
        AgentVersionEntity version = new AgentVersionEntity();
        version.setAgentId(entity.getAgentId());
        version.setTenantId(tenantId);
        version.setVersionNo(entity.getLatestVersionNo() + 1);
        version.setSnapshotJson(canonical);
        version.setChecksum(sha256(canonical));
        version.setPublishedBy(ownerUserId);
        version = versionRepository.saveAndFlush(version);
        entity.setLatestVersionNo(version.getVersionNo());
        entity.setStatus("PUBLISHED");
        agentRepository.save(entity);
        return agentVersionView(version);
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.AgentVersionView> listAgentVersions(String tenantId, String ownerUserId, String agentId) {
        requireOwnedAgent(tenantId, ownerUserId, agentId);
        return versionRepository.findByTenantIdAndAgentIdOrderByVersionNoDesc(tenantId, agentId)
                .stream().map(this::agentVersionView).toList();
    }

    @Transactional
    public ProductAssetDto.DeleteResponse deleteAgent(String tenantId, String ownerUserId, String agentId) {
        AgentEntity entity = requireOwnedAgent(tenantId, ownerUserId, agentId);
        if (deploymentRepository.countByTenantIdAndAssetTypeAndAssetIdAndStatusNot(
                tenantId, "AGENT", agentId, "ARCHIVED"
        ) > 0) {
            throw new AssetInUseException("该智能体仍被 API 发布使用，请先删除对应的 API 发布。");
        }
        if (entity.getLatestVersionNo() > 0 || "PUBLISHED".equals(entity.getStatus())) {
            entity.setStatus("ARCHIVED");
            agentRepository.saveAndFlush(entity);
            return new ProductAssetDto.DeleteResponse("ok", agentId, "ARCHIVED");
        }
        versionRepository.deleteAll(versionRepository.findByTenantIdAndAgentIdOrderByVersionNoDesc(tenantId, agentId));
        agentRepository.delete(entity);
        return new ProductAssetDto.DeleteResponse("ok", agentId, "DELETED");
    }

    @Transactional
    public ProductAssetDto.DeleteResponse deleteWorkflow(
            String tenantId, String ownerUserId, String workflowId
    ) {
        WorkflowDto.WorkflowView workflow = workflowService.get(tenantId, workflowId);
        if (!ownerUserId.equals(workflow.created_by())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workflow is not owned by current user");
        }
        if (deploymentRepository.countByTenantIdAndAssetTypeAndAssetIdAndStatusNot(
                tenantId, "WORKFLOW", workflowId, "ARCHIVED"
        ) > 0) {
            throw new AssetInUseException("该 Workflow 仍被 API 发布使用，请先删除对应的 API 发布。");
        }
        if (agentRepository.countByTenantIdAndWorkflowIdAndStatusNot(tenantId, workflowId, "ARCHIVED") > 0) {
            throw new AssetInUseException("该 Workflow 仍被智能体使用，请先解除关联或删除对应智能体。");
        }
        if (workflow.latest_version_no() > 0 || "PUBLISHED".equals(workflow.status())) {
            workflowService.archive(tenantId, ownerUserId, workflowId, null);
            return new ProductAssetDto.DeleteResponse("ok", workflowId, "ARCHIVED");
        }
        workflowService.deleteDraft(tenantId, workflowId);
        return new ProductAssetDto.DeleteResponse("ok", workflowId, "DELETED");
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.DeploymentView> listDeployments(String tenantId, String ownerUserId) {
        return deploymentRepository.findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(tenantId, ownerUserId)
                .stream().filter(item -> !"ARCHIVED".equals(item.getStatus())).map(this::deploymentView).toList();
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.DeploymentView> listPlatformDeployments() {
        return deploymentRepository.findAll().stream()
                .sorted((left, right) -> right.getUpdatedAt().compareTo(left.getUpdatedAt()))
                .map(this::deploymentView).toList();
    }

    @Transactional
    public ProductAssetDto.DeploymentView setPlatformDeploymentStatus(String deploymentId, String status) {
        DeploymentEntity entity = deploymentRepository.findById(required(deploymentId, "deployment id missing"))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "deployment not found"));
        String target = required(status, "deployment status missing").toUpperCase(Locale.ROOT);
        if (!List.of("ACTIVE", "PAUSED", "ARCHIVED").contains(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "status must be ACTIVE, PAUSED or ARCHIVED");
        }
        entity.setStatus(target);
        return deploymentView(deploymentRepository.saveAndFlush(entity));
    }

    @Transactional
    public ProductAssetDto.DeploymentView createDeployment(
            String tenantId, String ownerUserId, ProductAssetDto.DeploymentCreateRequest request
    ) {
        String type = required(request.asset_type(), "asset type missing").toUpperCase(Locale.ROOT);
        if (!type.equals("AGENT") && !type.equals("WORKFLOW")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "asset_type must be AGENT or WORKFLOW");
        }
        if (type.equals("AGENT")) {
            AgentEntity agent = requireOwnedAgent(tenantId, ownerUserId, request.asset_id());
            versionRepository.findByTenantIdAndAgentIdAndVersionNo(tenantId, agent.getAgentId(), request.asset_version_no())
                    .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "agent version not found"));
        } else {
            WorkflowDto.WorkflowView workflow = workflowService.get(tenantId, request.asset_id());
            if (!ownerUserId.equals(workflow.created_by())) {
                throw new ResponseStatusException(HttpStatus.FORBIDDEN, "workflow is not owned by current user");
            }
            workflowService.getVersion(tenantId, request.asset_id(), request.asset_version_no());
        }

        DeploymentEntity entity = new DeploymentEntity();
        entity.setTenantId(tenantId);
        entity.setOwnerUserId(ownerUserId);
        entity.setName(required(request.name(), "deployment name missing"));
        entity.setSlug(uniqueSlug(request.name()));
        entity.setAssetType(type);
        entity.setAssetId(request.asset_id());
        entity.setAssetVersionNo(request.asset_version_no());
        entity.setRateLimitPerMinute(request.rate_limit_per_minute() == null ? 60 : request.rate_limit_per_minute());
        return deploymentView(deploymentRepository.saveAndFlush(entity));
    }

    @Transactional
    public ProductAssetDto.DeploymentView setDeploymentStatus(
            String tenantId, String ownerUserId, String deploymentId, String status
    ) {
        DeploymentEntity entity = requireOwnedDeployment(tenantId, ownerUserId, deploymentId);
        String target = required(status, "deployment status missing").toUpperCase(Locale.ROOT);
        if (!List.of("ACTIVE", "PAUSED", "ARCHIVED").contains(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "status must be ACTIVE, PAUSED or ARCHIVED");
        }
        entity.setStatus(target);
        return deploymentView(deploymentRepository.saveAndFlush(entity));
    }

    @Transactional
    public ProductAssetDto.DeleteResponse deleteDeployment(
            String tenantId, String ownerUserId, String deploymentId
    ) {
        DeploymentEntity entity = requireOwnedDeployment(tenantId, ownerUserId, deploymentId);
        entity.setStatus("ARCHIVED");
        Instant revokedAt = Instant.now();
        List<DeploymentApiKeyEntity> keys = apiKeyRepository
                .findByTenantIdAndDeploymentIdOrderByCreatedAtDesc(tenantId, deploymentId);
        keys.stream().filter(key -> "ACTIVE".equals(key.getStatus())).forEach(key -> {
            key.setStatus("REVOKED");
            key.setRevokedAt(revokedAt);
        });
        apiKeyRepository.saveAll(keys);
        deploymentRepository.saveAndFlush(entity);
        return new ProductAssetDto.DeleteResponse("ok", deploymentId, "ARCHIVED");
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.ApiKeyView> listApiKeys(String tenantId, String ownerUserId, String deploymentId) {
        requireOwnedDeployment(tenantId, ownerUserId, deploymentId);
        return apiKeyRepository.findByTenantIdAndDeploymentIdOrderByCreatedAtDesc(tenantId, deploymentId)
                .stream().map(this::apiKeyView).toList();
    }

    @Transactional
    public IssuedApiKey createApiKey(
            String tenantId, String ownerUserId, String deploymentId, ProductAssetDto.ApiKeyCreateRequest request
    ) {
        DeploymentEntity deployment = requireOwnedDeployment(tenantId, ownerUserId, deploymentId);
        if (!"ACTIVE".equals(deployment.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "API keys can only be created for an active deployment");
        }
        String secret;
        String prefix;
        do {
            byte[] bytes = new byte[32];
            RANDOM.nextBytes(bytes);
            secret = KEY_PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
            prefix = secret.substring(0, KEY_LOOKUP_LENGTH);
        } while (apiKeyRepository.existsByKeyPrefix(prefix));

        DeploymentApiKeyEntity key = new DeploymentApiKeyEntity();
        key.setDeploymentId(deploymentId);
        key.setTenantId(tenantId);
        key.setOwnerUserId(ownerUserId);
        key.setName(required(request.name(), "API key name missing"));
        key.setKeyPrefix(prefix);
        key.setSecretHash(passwordEncoder.encode(secret));
        key.setExpiresAt(request.expires_at());
        key = apiKeyRepository.saveAndFlush(key);
        return new IssuedApiKey(apiKeyView(key), secret);
    }

    @Transactional
    public ProductAssetDto.ApiKeyView revokeApiKey(String tenantId, String ownerUserId, String apiKeyId) {
        DeploymentApiKeyEntity key = apiKeyRepository.findByApiKeyIdAndTenantId(apiKeyId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "API key not found"));
        requireOwnedDeployment(tenantId, ownerUserId, key.getDeploymentId());
        key.setStatus("REVOKED");
        key.setRevokedAt(Instant.now());
        return apiKeyView(apiKeyRepository.saveAndFlush(key));
    }

    @Transactional
    public ResolvedInvocation resolveInvocation(String slug, String secret) {
        DeploymentEntity deployment = deploymentRepository.findBySlugAndStatus(required(slug, "deployment slug missing"), "ACTIVE")
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "active deployment not found"));
        String normalized = required(secret, "API key missing");
        if (!normalized.startsWith(KEY_PREFIX) || normalized.length() < KEY_LOOKUP_LENGTH) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid API key");
        }
        DeploymentApiKeyEntity key = apiKeyRepository
                .findByKeyPrefixAndStatus(normalized.substring(0, KEY_LOOKUP_LENGTH), "ACTIVE")
                .filter(item -> deployment.getDeploymentId().equals(item.getDeploymentId()))
                .filter(item -> item.getExpiresAt() == null || item.getExpiresAt().isAfter(Instant.now()))
                .filter(item -> passwordEncoder.matches(normalized, item.getSecretHash()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid API key"));
        key.setLastUsedAt(Instant.now());
        apiKeyRepository.save(key);

        String workflowId = deployment.getAssetId();
        int workflowVersionNo = deployment.getAssetVersionNo();
        JsonNode agentSnapshot = null;
        if ("AGENT".equals(deployment.getAssetType())) {
            AgentVersionEntity version = versionRepository
                    .findByTenantIdAndAgentIdAndVersionNo(deployment.getTenantId(), deployment.getAssetId(), deployment.getAssetVersionNo())
                    .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT, "deployed agent version is missing"));
            agentSnapshot = readJson(version.getSnapshotJson());
            workflowId = agentSnapshot.path("workflow_id").asText("");
            workflowVersionNo = agentSnapshot.path("workflow_version_no").asInt(0);
        }
        if (!"AGENT".equals(deployment.getAssetType()) && (workflowId.isBlank() || workflowVersionNo < 1)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "deployment has no executable workflow version");
        }
        return new ResolvedInvocation(deployment, key, workflowId, workflowVersionNo, agentSnapshot);
    }

    @Transactional
    public DeploymentInvocationEntity beginInvocation(
            ResolvedInvocation resolved, String traceId, long inputBytes
    ) {
        DeploymentEntity deployment = resolved.deployment();
        invocationRepository.lockDeployment(deployment.getDeploymentId());
        long used = invocationRepository.countByDeploymentIdAndCreatedAtAfter(
                deployment.getDeploymentId(), Instant.now().minusSeconds(60)
        );
        DeploymentInvocationEntity entity = new DeploymentInvocationEntity();
        entity.setDeploymentId(deployment.getDeploymentId());
        entity.setApiKeyId(resolved.apiKey().getApiKeyId());
        entity.setTenantId(deployment.getTenantId());
        entity.setStatus(used >= deployment.getRateLimitPerMinute() ? "RATE_LIMITED" : "ACCEPTING");
        if (used >= deployment.getRateLimitPerMinute()) entity.setErrorCode("RATE_LIMIT_EXCEEDED");
        entity.setInputBytes(Math.max(0, inputBytes));
        entity.setTraceId(required(traceId, "trace id missing"));
        return invocationRepository.saveAndFlush(entity);
    }

    @Transactional
    public void finishInvocation(
            String invocationId,
            String runId,
            long startedNanos,
            String successStatus,
            long outputBytes,
            RuntimeException failure
    ) {
        DeploymentInvocationEntity entity = invocationRepository.findById(invocationId).orElseThrow();
        entity.setDurationMs(Math.max(0, (System.nanoTime() - startedNanos) / 1_000_000));
        if (failure == null) {
            entity.setRunId(runId);
            entity.setStatus(required(successStatus, "invocation success status missing"));
            entity.setOutputBytes(Math.max(0, outputBytes));
        } else {
            entity.setStatus("REJECTED");
            entity.setErrorCode(failure.getClass().getSimpleName());
        }
        invocationRepository.save(entity);
    }

    @Transactional
    public DeploymentInvocationEntity requireRunInvocation(ResolvedInvocation resolved, String runId) {
        return invocationRepository.findByDeploymentIdAndApiKeyIdAndRunId(
                        resolved.deployment().getDeploymentId(), resolved.apiKey().getApiKeyId(), required(runId, "run id missing")
                )
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "deployment run not found"));
    }

    @Transactional
    public DeploymentInvocationEntity syncRunInvocation(
            DeploymentInvocationEntity invocation,
            String runStatus,
            JsonNode output,
            String errorCode,
            Instant startedAt,
            Instant finishedAt
    ) {
        String normalized = required(runStatus, "run status missing").toUpperCase(Locale.ROOT);
        invocation.setStatus(switch (normalized) {
            case "SUCCEEDED" -> "COMPLETED";
            case "FAILED" -> "FAILED";
            case "CANCELLED" -> "CANCELLED";
            default -> "ACCEPTED";
        });
        invocation.setErrorCode(errorCode == null ? "" : errorCode);
        if (output != null && !output.isNull() && !output.isMissingNode()) {
            invocation.setOutputBytes(writeJson(output).getBytes(StandardCharsets.UTF_8).length);
        }
        if (startedAt != null && finishedAt != null) {
            invocation.setDurationMs(Math.max(0, java.time.Duration.between(startedAt, finishedAt).toMillis()));
        }
        return invocationRepository.save(invocation);
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.InvocationView> listInvocations(
            String tenantId, String ownerUserId, String deploymentId
    ) {
        requireOwnedDeployment(tenantId, ownerUserId, deploymentId);
        return invocationRepository.findTop100ByTenantIdAndDeploymentIdOrderByCreatedAtDesc(tenantId, deploymentId)
                .stream().map(this::invocationView).toList();
    }

    @Transactional(readOnly = true)
    public List<ProductAssetDto.InvocationView> listPlatformInvocations() {
        return invocationRepository.findTop200ByOrderByCreatedAtDesc().stream().map(this::invocationView).toList();
    }

    private void applyAgent(AgentEntity entity, ProductAssetDto.AgentUpsertRequest request) {
        entity.setName(required(request.name(), "agent name missing"));
        entity.setDescription(clean(request.description()));
        entity.setInstructions(clean(request.instructions()));
        entity.setModelConfigJson(writeJson(normalizeAgentModelConfig(request.model_config())));
        ArrayNode kbIds = objectMapper.createArrayNode();
        if (request.knowledge_base_ids() != null) request.knowledge_base_ids().stream()
                .filter(value -> value != null && !value.isBlank()).distinct().forEach(kbIds::add);
        entity.setKnowledgeBaseIdsJson(writeJson(kbIds));
        entity.setWorkflowId(request.workflow_id() == null || request.workflow_id().isBlank() ? null : request.workflow_id().trim());
        String visibility = request.visibility() == null ? "PRIVATE" : request.visibility().trim().toUpperCase(Locale.ROOT);
        if (!List.of("PRIVATE", "WORKSPACE").contains(visibility)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "visibility must be PRIVATE or WORKSPACE");
        }
        entity.setVisibility(visibility);
    }

    private ObjectNode normalizeAgentModelConfig(JsonNode raw) {
        JsonNode source = raw != null && raw.isObject() ? raw : objectMapper.createObjectNode();
        ObjectNode config = objectMapper.createObjectNode();
        config.put("role", clean(source.path("role").asText("")));
        config.put("response_style", clean(source.path("response_style").asText("")));
        config.put("model_endpoint_id", clean(source.path("model_endpoint_id").asText("")));
        config.put("temperature", boundedDouble(source.path("temperature").asDouble(0.2), 0, 2, "temperature"));
        config.put("top_p", boundedDouble(source.path("top_p").asDouble(1.0), 0.01, 1, "top_p"));
        config.put("max_tokens", boundedInt(source.path("max_tokens").asInt(4096), 64, 32768, "max_tokens"));
        String prohibitedRules = clean(source.path("prohibited_rules").asText(""));
        config.put("prohibited_rules", prohibitedRules.length() > 12000 ? prohibitedRules.substring(0, 12000) : prohibitedRules);

        JsonNode memoryRaw = source.path("memory");
        ObjectNode memory = objectMapper.createObjectNode();
        String memoryMode = clean(memoryRaw.path("mode").asText("long_term")).toLowerCase(Locale.ROOT);
        if (!List.of("none", "short_term", "long_term").contains(memoryMode)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "memory.mode is invalid");
        }
        String preset = clean(memoryRaw.path("preset").asText("private_assistant")).toLowerCase(Locale.ROOT);
        if (!List.of("private_assistant", "customer_service", "consulting", "stateless", "custom").contains(preset)) preset = "custom";
        memory.put("mode", memoryMode);
        memory.put("preset", preset);
        memory.put("recent_messages_limit", boundedInt(memoryRaw.path("recent_messages_limit").asInt(12), 1, 50, "memory.recent_messages_limit"));
        memory.put("context_token_budget", boundedInt(memoryRaw.path("context_token_budget").asInt(1800), 200, 8000, "memory.context_token_budget"));
        memory.put("compress_trigger_messages", boundedInt(memoryRaw.path("compress_trigger_messages").asInt(24), 8, 200, "memory.compress_trigger_messages"));
        memory.put("summary_enabled", memoryRaw.path("summary_enabled").asBoolean(true));
        memory.put("user_profile_enabled", memoryRaw.path("user_profile_enabled").asBoolean(true));
        memory.put("episodic_enabled", memoryRaw.path("episodic_enabled").asBoolean(true));
        memory.put("session_recall_enabled", memoryRaw.path("session_recall_enabled").asBoolean(true));
        memory.put("memory_item_max", boundedInt(memoryRaw.path("memory_item_max").asInt(200), 20, 2000, "memory.memory_item_max"));
        config.set("memory", memory);

        ArrayNode variables = objectMapper.createArrayNode();
        JsonNode variablesRaw = source.path("prompt_variables");
        if (variablesRaw.isArray()) for (JsonNode item : variablesRaw) {
            if (variables.size() >= 8 || !"current_datetime".equals(item.path("key").asText())) continue;
            String calendar = item.path("calendar").asText("both").toLowerCase(Locale.ROOT);
            if (!List.of("gregorian", "lunar", "both").contains(calendar)) calendar = "both";
            ObjectNode variable = objectMapper.createObjectNode();
            variable.put("key", "current_datetime");
            variable.put("enabled", item.path("enabled").asBoolean(true));
            variable.put("calendar", calendar);
            String timezone = clean(item.path("timezone").asText("Asia/Shanghai"));
            variable.put("timezone", timezone.length() > 64 ? "Asia/Shanghai" : timezone);
            variables.add(variable);
        }
        config.set("prompt_variables", variables);

        ArrayNode skills = objectMapper.createArrayNode();
        JsonNode skillsRaw = source.path("skills");
        if (skillsRaw.isArray()) for (JsonNode item : skillsRaw) {
            if (skills.size() >= 20) break;
            String skillId = clean(item.path("skill_id").asText(""));
            if (skillId.isBlank() || item.path("version").asInt(0) < 1) continue;
            ObjectNode skill = objectMapper.createObjectNode();
            skill.put("skill_id", skillId.length() > 128 ? skillId.substring(0, 128) : skillId);
            String name = clean(item.path("name").asText(""));
            skill.put("name", name.length() > 128 ? name.substring(0, 128) : name);
            skill.put("version", item.path("version").asInt());
            skills.add(skill);
        }
        config.set("skills", skills);

        JsonNode policyRaw = source.path("tool_policy");
        ObjectNode policy = objectMapper.createObjectNode();
        String policyMode = clean(policyRaw.path("mode").asText("auto")).toLowerCase(Locale.ROOT);
        if (!List.of("none", "auto", "required").contains(policyMode)) policyMode = "auto";
        policy.put("mode", policyMode);
        policy.put("max_rounds", boundedInt(policyRaw.path("max_rounds").asInt(3), 1, 8, "tool_policy.max_rounds"));
        policy.put("timeout_seconds", boundedInt(policyRaw.path("timeout_seconds").asInt(30), 1, 120, "tool_policy.timeout_seconds"));
        policy.put("result_max_chars", boundedInt(policyRaw.path("result_max_chars").asInt(12000), 500, 50000, "tool_policy.result_max_chars"));
        policy.put("allow_write_tools", policyRaw.path("allow_write_tools").asBoolean(false));
        String failureMode = clean(policyRaw.path("failure_mode").asText("continue")).toLowerCase(Locale.ROOT);
        policy.put("failure_mode", "fail".equals(failureMode) ? "fail" : "continue");
        config.set("tool_policy", policy);

        ArrayNode mcpTools = objectMapper.createArrayNode();
        JsonNode mcpRaw = source.path("mcp_tools");
        if (mcpRaw.isArray()) for (JsonNode item : mcpRaw) {
            if (mcpTools.size() >= 50) break;
            String connectionId = clean(item.path("connection_id").asText(""));
            String toolName = clean(item.path("tool_name").asText(""));
            if (connectionId.isBlank() || toolName.isBlank()) continue;
            ObjectNode selected = objectMapper.createObjectNode();
            selected.put("connection_id", connectionId.length() > 128 ? connectionId.substring(0, 128) : connectionId);
            selected.put("tool_name", toolName.length() > 256 ? toolName.substring(0, 256) : toolName);
            mcpTools.add(selected);
        }
        config.set("mcp_tools", mcpTools);

        ArrayNode javascriptTools = objectMapper.createArrayNode();
        JsonNode javascriptRaw = source.path("javascript_tools");
        if (javascriptRaw.isArray()) for (JsonNode item : javascriptRaw) {
            if (javascriptTools.size() >= 50) break;
            String toolId = clean(item.path("tool_id").asText(""));
            if (toolId.isBlank()) continue;
            ObjectNode selected = objectMapper.createObjectNode();
            selected.put("tool_id", toolId.length() > 128 ? toolId.substring(0,128) : toolId);
            selected.put("version", Math.max(1,item.path("version").asInt(1)));
            javascriptTools.add(selected);
        }
        config.set("javascript_tools", javascriptTools);

        JsonNode retrievalRaw = source.path("retrieval");
        ObjectNode retrieval = objectMapper.createObjectNode();
        retrieval.put("enabled", retrievalRaw.path("enabled").asBoolean(true));
        retrieval.put("top_k", boundedInt(retrievalRaw.path("top_k").asInt(6), 1, 50, "retrieval.top_k"));
        String rankMethod = clean(retrievalRaw.path("rank_method").asText("vector")).toLowerCase(Locale.ROOT);
        if (!List.of("vector", "hybrid", "rrf", "keyword").contains(rankMethod)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "retrieval.rank_method is invalid");
        }
        retrieval.put("rank_method", rankMethod);
        retrieval.put("score_threshold", boundedDouble(
                retrievalRaw.path("score_threshold").asDouble(0), 0, 1, "retrieval.score_threshold"
        ));
        retrieval.put("rerank_enabled", retrievalRaw.path("rerank_enabled").asBoolean(false));
        retrieval.put("rerank_k", boundedInt(
                retrievalRaw.path("rerank_k").asInt(30), 1, 100, "retrieval.rerank_k"
        ));
        retrieval.put("rerank_required", retrievalRaw.path("rerank_required").asBoolean(false));
        config.set("retrieval", retrieval);

        ArrayNode examples = objectMapper.createArrayNode();
        JsonNode examplesRaw = source.path("reply_examples");
        if (examplesRaw.isArray()) {
            int count = 0;
            for (JsonNode item : examplesRaw) {
                if (count >= 8) break;
                String user = clean(item.path("user").asText(""));
                String assistant = clean(item.path("assistant").asText(""));
                if (user.isBlank() && assistant.isBlank()) continue;
                ObjectNode example = objectMapper.createObjectNode();
                example.put("user", user.length() > 4000 ? user.substring(0, 4000) : user);
                example.put("assistant", assistant.length() > 8000 ? assistant.substring(0, 8000) : assistant);
                examples.add(example);
                count++;
            }
        }
        config.set("reply_examples", examples);
        return config;
    }

    private int boundedInt(int value, int min, int max, String field) {
        if (value < min || value > max) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, field + " is out of range");
        }
        return value;
    }

    private double boundedDouble(double value, double min, double max, String field) {
        if (!Double.isFinite(value) || value < min || value > max) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, field + " is out of range");
        }
        return value;
    }

    private AgentEntity requireOwnedAgent(String tenantId, String ownerUserId, String agentId) {
        AgentEntity entity = agentRepository.findByAgentIdAndTenantId(required(agentId, "agent id missing"), tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "agent not found"));
        if (!ownerUserId.equals(entity.getOwnerUserId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "agent is not owned by current user");
        }
        return entity;
    }

    private DeploymentEntity requireOwnedDeployment(String tenantId, String ownerUserId, String deploymentId) {
        DeploymentEntity entity = deploymentRepository.findByDeploymentIdAndTenantId(deploymentId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "deployment not found"));
        if (!ownerUserId.equals(entity.getOwnerUserId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "deployment is not owned by current user");
        }
        return entity;
    }

    private String uniqueSlug(String name) {
        String stem = clean(name).toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "-")
                .replaceAll("^-+|-+$", "");
        if (stem.isBlank()) stem = "app";
        if (stem.length() > 64) stem = stem.substring(0, 64).replaceAll("-+$", "");
        String slug;
        do slug = stem + "-" + UUID.randomUUID().toString().substring(0, 8);
        while (deploymentRepository.existsBySlug(slug));
        return slug;
    }

    private ProductAssetDto.AgentView agentView(AgentEntity entity) {
        List<String> kbIds = new ArrayList<>();
        JsonNode parsed = readJson(entity.getKnowledgeBaseIdsJson());
        if (parsed.isArray()) parsed.forEach(item -> kbIds.add(item.asText()));
        return new ProductAssetDto.AgentView(
                entity.getAgentId(), entity.getTenantId(), entity.getOwnerUserId(), entity.getName(), entity.getDescription(),
                entity.getInstructions(), readJson(entity.getModelConfigJson()), kbIds, entity.getWorkflowId(),
                entity.getVisibility(), entity.getStatus(), entity.getLatestVersionNo(), entity.getRowVersion(),
                entity.getCreatedAt(), entity.getUpdatedAt()
        );
    }

    private ProductAssetDto.AgentVersionView agentVersionView(AgentVersionEntity entity) {
        return new ProductAssetDto.AgentVersionView(
                entity.getVersionId(), entity.getAgentId(), entity.getVersionNo(), readJson(entity.getSnapshotJson()),
                entity.getChecksum(), entity.getPublishedBy(), entity.getPublishedAt()
        );
    }

    private ProductAssetDto.DeploymentView deploymentView(DeploymentEntity entity) {
        return new ProductAssetDto.DeploymentView(
                entity.getDeploymentId(), entity.getTenantId(), entity.getOwnerUserId(), entity.getName(), entity.getSlug(),
                "/api/v1/deployments/" + entity.getSlug() + "/invoke", entity.getAssetType(), entity.getAssetId(),
                entity.getAssetVersionNo(), entity.getStatus(), entity.getRateLimitPerMinute(), entity.getCreatedAt(), entity.getUpdatedAt()
        );
    }

    private ProductAssetDto.ApiKeyView apiKeyView(DeploymentApiKeyEntity entity) {
        return new ProductAssetDto.ApiKeyView(
                entity.getApiKeyId(), entity.getDeploymentId(), entity.getName(), entity.getKeyPrefix(), entity.getStatus(),
                entity.getExpiresAt(), entity.getLastUsedAt(), entity.getCreatedAt(), entity.getRevokedAt()
        );
    }

    private ProductAssetDto.InvocationView invocationView(DeploymentInvocationEntity entity) {
        return new ProductAssetDto.InvocationView(
                entity.getInvocationId(), entity.getDeploymentId(), entity.getApiKeyId(), entity.getTenantId(),
                entity.getRunId(), entity.getStatus(), entity.getInputBytes(), entity.getOutputBytes(),
                entity.getDurationMs(), entity.getErrorCode(), entity.getTraceId(), entity.getCreatedAt()
        );
    }

    private JsonNode readJson(String value) {
        try { return objectMapper.readTree(value); }
        catch (JsonProcessingException error) { throw new IllegalStateException("stored product asset JSON is invalid", error); }
    }

    private String writeJson(JsonNode value) {
        try { return objectMapper.writeValueAsString(value); }
        catch (JsonProcessingException error) { throw new IllegalArgumentException("asset JSON cannot be serialized", error); }
    }

    private String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    private String required(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }

    private String clean(String value) { return value == null ? "" : value.trim(); }

    public record IssuedApiKey(ProductAssetDto.ApiKeyView item, String secret) {}
    public record ResolvedInvocation(
            DeploymentEntity deployment,
            DeploymentApiKeyEntity apiKey,
            String workflowId,
            int workflowVersionNo,
            JsonNode agentSnapshot
    ) {}
}
