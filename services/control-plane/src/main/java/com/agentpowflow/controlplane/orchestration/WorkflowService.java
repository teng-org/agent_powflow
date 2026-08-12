package com.agentpowflow.controlplane.orchestration;

import com.agentpowflow.controlplane.canvas.CanvasDslMapper;
import com.agentpowflow.controlplane.canvas.CanvasModels;
import com.agentpowflow.controlplane.canvas.DslValidator;
import com.agentpowflow.controlplane.integrations.JavaScriptToolService;
import com.agentpowflow.controlplane.web.dto.WorkflowDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;

@Service
public class WorkflowService {
    private final WorkflowRepository workflowRepository;
    private final WorkflowVersionRepository versionRepository;
    private final ObjectMapper objectMapper;
    private final CanvasDslMapper canvasDslMapper;
    private final DslValidator dslValidator;
    private final JavaScriptToolService javaScriptToolService;

    public WorkflowService(
            WorkflowRepository workflowRepository,
            WorkflowVersionRepository versionRepository,
            ObjectMapper objectMapper,
            CanvasDslMapper canvasDslMapper,
            DslValidator dslValidator,
            JavaScriptToolService javaScriptToolService
    ) {
        this.workflowRepository = workflowRepository;
        this.versionRepository = versionRepository;
        this.objectMapper = objectMapper;
        this.canvasDslMapper = canvasDslMapper;
        this.dslValidator = dslValidator;
        this.javaScriptToolService = javaScriptToolService;
    }

    @Transactional(readOnly = true)
    public List<WorkflowDto.WorkflowView> list(String tenantId) {
        return workflowRepository.findByTenantIdOrderByUpdatedAtDesc(tenantId).stream().map(this::toView).toList();
    }

    @Transactional(readOnly = true)
    public WorkflowDto.WorkflowView get(String tenantId, String workflowId) {
        return toView(requireWorkflow(tenantId, workflowId));
    }

    @Transactional
    public WorkflowDto.WorkflowView create(
            String tenantId,
            String actorId,
            String name,
            String description,
            JsonNode dsl,
            JsonNode layout
    ) {
        WorkflowEntity entity = new WorkflowEntity();
        entity.setTenantId(requireText(tenantId, "tenant id missing"));
        entity.setName(requireText(name, "workflow name missing"));
        entity.setDescription(description == null ? "" : description.trim());
        entity.setDraftDslJson(writeJson(dsl));
        entity.setDraftLayoutJson(writeJson(layoutOrEmpty(layout)));
        entity.setCreatedBy(requireText(actorId, "actor id missing"));
        entity.setUpdatedBy(actorId);
        return toView(workflowRepository.saveAndFlush(entity));
    }

    @Transactional
    public WorkflowDto.WorkflowView saveDraft(
            String tenantId,
            String actorId,
            String workflowId,
            String name,
            String description,
            JsonNode dsl,
            JsonNode layout,
            Long expectedRevision
    ) {
        WorkflowEntity entity = requireWorkflow(tenantId, workflowId);
        assertRevision(entity, expectedRevision);
        if ("ARCHIVED".equals(entity.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "archived workflow is read-only");
        }
        if (name != null) entity.setName(requireText(name, "workflow name missing"));
        if (description != null) entity.setDescription(description.trim());
        entity.setDraftDslJson(writeJson(dsl));
        entity.setDraftLayoutJson(writeJson(layoutOrEmpty(layout)));
        entity.setUpdatedBy(requireText(actorId, "actor id missing"));
        if (entity.getLatestVersionNo() == 0) entity.setStatus("DRAFT");
        // Flush before mapping so the response carries the database-assigned @Version value.
        // Clients use that revision immediately for publish, so returning the pre-flush value
        // makes a valid save-draft -> publish sequence fail with a false 409 conflict.
        return toView(workflowRepository.saveAndFlush(entity));
    }

    @Transactional(readOnly = true)
    public CanvasModels.ValidationResult validate(String tenantId, String workflowId) {
        WorkflowEntity entity = requireWorkflow(tenantId, workflowId);
        return validateJson(readJson(entity.getDraftDslJson()));
    }

    @Transactional
    public WorkflowDto.VersionView publish(String tenantId, String actorId, String workflowId, Long expectedRevision) {
        WorkflowEntity workflow = requireWorkflow(tenantId, workflowId);
        assertRevision(workflow, expectedRevision);
        if ("ARCHIVED".equals(workflow.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "archived workflow cannot be published");
        }
        CanvasModels.ValidationResult validation = validateJson(readJson(workflow.getDraftDslJson()));
        if (!validation.valid()) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "workflow DSL validation failed");
        }
        JsonNode pinnedDsl = pinJavaScriptTools(tenantId, actorId, readJson(workflow.getDraftDslJson()));
        int nextVersion = workflow.getLatestVersionNo() + 1;
        WorkflowVersionEntity version = new WorkflowVersionEntity();
        version.setWorkflowId(workflow.getWorkflowId());
        version.setTenantId(tenantId);
        version.setVersionNo(nextVersion);
        version.setDslJson(writeJson(pinnedDsl));
        version.setLayoutJson(workflow.getDraftLayoutJson());
        version.setChecksum(checksum(writeJson(pinnedDsl), workflow.getDraftLayoutJson()));
        version.setPublishedBy(requireText(actorId, "actor id missing"));
        WorkflowVersionEntity saved = versionRepository.save(version);

        workflow.setLatestVersionNo(nextVersion);
        workflow.setStatus("PUBLISHED");
        workflow.setUpdatedBy(actorId);
        workflowRepository.saveAndFlush(workflow);
        return toVersionView(saved);
    }

    @Transactional
    public WorkflowDto.WorkflowView archive(String tenantId, String actorId, String workflowId, Long expectedRevision) {
        WorkflowEntity workflow = requireWorkflow(tenantId, workflowId);
        assertRevision(workflow, expectedRevision);
        workflow.setStatus("ARCHIVED");
        workflow.setUpdatedBy(requireText(actorId, "actor id missing"));
        return toView(workflowRepository.saveAndFlush(workflow));
    }

    @Transactional(readOnly = true)
    public List<WorkflowDto.VersionView> listVersions(String tenantId, String workflowId) {
        requireWorkflow(tenantId, workflowId);
        return versionRepository.findByWorkflowIdAndTenantIdOrderByVersionNoDesc(workflowId, tenantId).stream()
                .map(this::toVersionView)
                .toList();
    }

    @Transactional(readOnly = true)
    public WorkflowDto.VersionView getVersion(String tenantId, String workflowId, int versionNo) {
        requireWorkflow(tenantId, workflowId);
        return versionRepository.findByWorkflowIdAndTenantIdAndVersionNo(workflowId, tenantId, versionNo)
                .map(this::toVersionView)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "workflow version not found"));
    }

    @Transactional
    public void deleteArchived(String tenantId, String workflowId) {
        WorkflowEntity workflow = requireWorkflow(tenantId, workflowId);
        if (!"ARCHIVED".equals(workflow.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "workflow must be archived before deletion");
        }
        List<WorkflowVersionEntity> versions = versionRepository
                .findByWorkflowIdAndTenantIdOrderByVersionNoDesc(workflowId, tenantId);
        versionRepository.deleteAll(versions);
        workflowRepository.delete(workflow);
    }

    @Transactional
    public void deleteDraft(String tenantId, String workflowId) {
        WorkflowEntity workflow = requireWorkflow(tenantId, workflowId);
        if (workflow.getLatestVersionNo() > 0 || !"DRAFT".equals(workflow.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "only an unpublished draft can be deleted");
        }
        workflowRepository.delete(workflow);
    }

    public CanvasModels.ValidationResult validateJson(JsonNode dsl) {
        return dslValidator.validate(canvasDslMapper.fromJson(dsl));
    }

    private WorkflowEntity requireWorkflow(String tenantId, String workflowId) {
        return workflowRepository.findByWorkflowIdAndTenantId(workflowId, tenantId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "workflow not found"));
    }

    private void assertRevision(WorkflowEntity entity, Long expectedRevision) {
        if (expectedRevision != null && expectedRevision != entity.getRowVersion()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "workflow revision conflict");
        }
    }

    private WorkflowDto.WorkflowView toView(WorkflowEntity entity) {
        return new WorkflowDto.WorkflowView(
                entity.getWorkflowId(), entity.getTenantId(), entity.getName(), entity.getDescription(), entity.getStatus(),
                entity.getLatestVersionNo(), entity.getRowVersion(), readJson(entity.getDraftDslJson()),
                readJson(entity.getDraftLayoutJson()), entity.getCreatedBy(), entity.getUpdatedBy(),
                entity.getCreatedAt(), entity.getUpdatedAt()
        );
    }

    private WorkflowDto.VersionView toVersionView(WorkflowVersionEntity entity) {
        return new WorkflowDto.VersionView(
                entity.getVersionId(), entity.getWorkflowId(), entity.getTenantId(), entity.getVersionNo(),
                readJson(entity.getDslJson()), readJson(entity.getLayoutJson()), entity.getChecksum(),
                entity.getPublishedBy(), entity.getPublishedAt()
        );
    }

    private JsonNode layoutOrEmpty(JsonNode layout) {
        return layout == null || layout.isNull() ? JsonNodeFactory.instance.objectNode() : layout;
    }

    private JsonNode pinJavaScriptTools(String tenantId,String actorId,JsonNode dsl){
        JsonNode copy=dsl.deepCopy();JsonNode nodes=copy.path("nodes");if(!nodes.isArray())return copy;
        for(JsonNode node:nodes){if(!"tool.javascript".equals(node.path("type").asText()))continue;JsonNode config=node.path("config");String toolId=config.path("tool_id").asText("");int version=config.path("version").asInt(0);if(toolId.isBlank()||version<1)throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,"JavaScript tool node is not configured");var owned=javaScriptToolService.resolveOwned(tenantId,actorId,toolId);JsonNode definition=javaScriptToolService.resolveVersion(tenantId,toolId,version);if(!definition.path("allow_workflow").asBoolean())throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,"JavaScript tool is not assigned to Workflow");((com.fasterxml.jackson.databind.node.ObjectNode)config).put("tool_name",owned.name()).set("definition",definition);}
        return copy;
    }

    private String writeJson(JsonNode value) {
        try {
            return objectMapper.writeValueAsString(value == null ? JsonNodeFactory.instance.objectNode() : value);
        } catch (JsonProcessingException ex) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid JSON payload", ex);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value == null || value.isBlank() ? "{}" : value);
        } catch (JsonProcessingException ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "stored workflow JSON is invalid", ex);
        }
    }

    private String checksum(String dsl, String layout) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest((dsl + "\n" + layout).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException("SHA-256 unavailable", ex);
        }
    }

    private String requireText(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }
}
