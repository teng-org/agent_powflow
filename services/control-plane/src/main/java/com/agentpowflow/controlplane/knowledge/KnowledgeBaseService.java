package com.agentpowflow.controlplane.knowledge;

import com.agentpowflow.controlplane.domain.TenantRepository;
import com.agentpowflow.controlplane.web.dto.KnowledgeBaseDto;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.LinkedHashMap;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

@Service
public class KnowledgeBaseService {
    private static final Set<String> VISIBILITIES = Set.of("PRIVATE", "WORKSPACE", "SHARED_ALL");

    private final KnowledgeBaseRepository knowledgeBaseRepository;
    private final KnowledgeBaseBindingRepository bindingRepository;
    private final TenantRepository tenantRepository;
    private final KnowledgeExecutionClient executionClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public KnowledgeBaseService(
            KnowledgeBaseRepository knowledgeBaseRepository,
            KnowledgeBaseBindingRepository bindingRepository,
            TenantRepository tenantRepository,
            KnowledgeExecutionClient executionClient
    ) {
        this.knowledgeBaseRepository = knowledgeBaseRepository;
        this.bindingRepository = bindingRepository;
        this.tenantRepository = tenantRepository;
        this.executionClient = executionClient;
    }

    @Transactional(readOnly = true)
    public List<KnowledgeBaseDto.KnowledgeBaseView> list(String tenantId) {
        return knowledgeBaseRepository.findByTenantIdOrderByCreatedAtAsc(requireText(tenantId, "tenant id missing"))
                .stream().map(this::toView).toList();
    }

    @Transactional(readOnly = true)
    public List<KnowledgeBaseDto.KnowledgeBaseView> listOwned(String tenantId, String ownerUserId) {
        return knowledgeBaseRepository.findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(
                        requireText(tenantId, "tenant id missing"), requireText(ownerUserId, "owner user id missing"))
                .stream().map(this::toView).toList();
    }

    @Transactional(readOnly = true)
    public List<KnowledgeBaseDto.KnowledgeBaseView> listAccessible(String tenantId, String ownerUserId) {
        String tenant = requireText(tenantId, "tenant id missing");
        String owner = requireText(ownerUserId, "owner user id missing");
        LinkedHashMap<String, KnowledgeBaseEntity> items = new LinkedHashMap<>();
        knowledgeBaseRepository.findByTenantIdAndOwnerUserIdOrderByUpdatedAtDesc(tenant, owner)
                .forEach(item -> items.put(item.getTenantId() + "/" + item.getKbId(), item));
        knowledgeBaseRepository.findByTenantIdAndVisibilityOrderByUpdatedAtDesc(tenant, "WORKSPACE")
                .forEach(item -> items.put(item.getTenantId() + "/" + item.getKbId(), item));
        bindingRepository.findByTargetTenantIdOrderByUpdatedAtDesc(tenant).forEach(binding ->
                knowledgeBaseRepository.findByTenantIdAndKbId(binding.getOwnerTenantId(), binding.getKbId())
                        .filter(item -> !item.isFrozen())
                        .ifPresent(item -> items.put(item.getTenantId() + "/" + item.getKbId(), item))
        );
        return items.values().stream().filter(item -> !item.isFrozen()).map(this::toView).toList();
    }

    @Transactional(readOnly = true)
    public void assertOwned(String tenantId, String ownerUserId, String kbId) {
        requireOwned(tenantId, ownerUserId, kbId);
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView create(String tenantId, String name) {
        return create(tenantId, "system", name);
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView create(String tenantId, String ownerUserId, String name) {
        return create(tenantId, ownerUserId, name, "", List.of(), null);
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView create(
            String tenantId, String ownerUserId, String name, String description, List<String> tags, String visibility
    ) {
        String tenant = requireText(tenantId, "tenant id missing");
        knowledgeBaseRepository.lockTenant(tenant);
        KnowledgeBaseEntity entity = new KnowledgeBaseEntity();
        entity.setTenantId(tenant);
        entity.setKbId(UUID.randomUUID().toString());
        entity.setName(resolveUniqueName(tenant, name, null));
        entity.setDescription(normalizeDescription(description));
        entity.setTagsJson(writeTags(tags));
        entity.setOwnerUserId(requireText(ownerUserId, "owner user id missing"));
        String defaultVisibility = "platform".equals(tenant) ? "SHARED_ALL" : "PRIVATE";
        entity.setVisibility(userVisibility(visibility, defaultVisibility));
        entity.setMetadataOwner("CONTROL");
        return toView(knowledgeBaseRepository.saveAndFlush(entity));
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView renameOwned(String tenantId, String ownerUserId, String kbId, String name) {
        requireOwned(tenantId, ownerUserId, kbId);
        return rename(tenantId, kbId, name);
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView updateOwned(
            String tenantId, String ownerUserId, String kbId, String name,
            String description, List<String> tags, String visibility
    ) {
        KnowledgeBaseEntity entity = requireOwned(tenantId, ownerUserId, kbId);
        knowledgeBaseRepository.lockTenant(tenantId);
        entity.setName(resolveUniqueName(tenantId, name, kbId));
        entity.setDescription(normalizeDescription(description));
        entity.setTagsJson(writeTags(tags));
        entity.setVisibility(userVisibility(visibility, entity.getVisibility()));
        entity.setMetadataOwner("CONTROL");
        return toView(knowledgeBaseRepository.saveAndFlush(entity));
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView setOwnedVisibility(
            String tenantId, String ownerUserId, String kbId, String visibility
    ) {
        requireOwned(tenantId, ownerUserId, kbId);
        String target = requireText(visibility, "visibility missing").toUpperCase(Locale.ROOT);
        if (!Set.of("PRIVATE", "WORKSPACE").contains(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "user visibility must be PRIVATE or WORKSPACE");
        }
        return setVisibility(tenantId, kbId, target);
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView rename(String tenantId, String kbId, String name) {
        String tenant = requireText(tenantId, "tenant id missing");
        knowledgeBaseRepository.lockTenant(tenant);
        KnowledgeBaseEntity entity = requireKnowledgeBase(tenant, kbId);
        entity.setName(resolveUniqueName(tenant, name, entity.getKbId()));
        entity.setMetadataOwner("CONTROL");
        return toView(knowledgeBaseRepository.saveAndFlush(entity));
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView setVisibility(String tenantId, String kbId, String visibility) {
        KnowledgeBaseEntity entity = requireKnowledgeBase(tenantId, kbId);
        String target = requireText(visibility, "visibility missing").toUpperCase(Locale.ROOT);
        if (!VISIBILITIES.contains(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "visibility must be PRIVATE, WORKSPACE or SHARED_ALL");
        }
        entity.setVisibility(target);
        entity.setMetadataOwner("CONTROL");
        return toView(knowledgeBaseRepository.saveAndFlush(entity));
    }

    @Transactional
    public KnowledgeBaseDto.KnowledgeBaseView setFrozen(String tenantId, String kbId, boolean frozen) {
        KnowledgeBaseEntity entity = requireKnowledgeBase(tenantId, kbId);
        entity.setFrozen(frozen);
        entity.setMetadataOwner("CONTROL");
        return toView(knowledgeBaseRepository.saveAndFlush(entity));
    }

    @Transactional(readOnly = true)
    public List<KnowledgeBaseDto.BindingView> listBindings(String tenantId, String kbId) {
        requireKnowledgeBase(tenantId, kbId);
        return bindingRepository.findByOwnerTenantIdAndKbIdOrderByUpdatedAtDesc(tenantId, kbId)
                .stream().map(this::toBindingView).toList();
    }

    @Transactional
    public KnowledgeBaseDto.BindingView bind(String tenantId, String actorId, String kbId, String targetTenantId) {
        String owner = requireText(tenantId, "tenant id missing");
        String target = requireText(targetTenantId, "target tenant id missing");
        requireKnowledgeBase(owner, kbId);
        if (owner.equals(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "owner tenant does not need an explicit binding");
        }
        if (!tenantRepository.existsById(target)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "target tenant not found");
        }
        KnowledgeBaseBindingId id = new KnowledgeBaseBindingId(owner, kbId, target);
        KnowledgeBaseBindingEntity entity = bindingRepository.findById(id).orElseGet(KnowledgeBaseBindingEntity::new);
        entity.setOwnerTenantId(owner);
        entity.setKbId(requireText(kbId, "knowledge base id missing"));
        entity.setTargetTenantId(target);
        entity.setCreatedBy(requireText(actorId, "actor id missing"));
        return toBindingView(bindingRepository.saveAndFlush(entity));
    }

    @Transactional
    public void unbind(String tenantId, String kbId, String targetTenantId) {
        requireKnowledgeBase(tenantId, kbId);
        KnowledgeBaseBindingId id = new KnowledgeBaseBindingId(tenantId, kbId, targetTenantId);
        if (!bindingRepository.existsById(id)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base binding not found");
        }
        bindingRepository.deleteById(id);
    }

    @Transactional
    public void delete(String tenantId, String kbId) {
        KnowledgeBaseEntity entity = requireKnowledgeBase(tenantId, kbId);
        executionClient.deleteKnowledgeBase(tenantId, kbId);
        bindingRepository.deleteByOwnerTenantIdAndKbId(tenantId, kbId);
        knowledgeBaseRepository.delete(entity);
    }

    @Transactional
    public void deleteOwned(String tenantId, String ownerUserId, String kbId) {
        requireOwned(tenantId, ownerUserId, kbId);
        delete(tenantId, kbId);
    }

    private String resolveUniqueName(String tenantId, String requestedName, String excludedKbId) {
        String base = requireText(requestedName, "knowledge base name missing");
        Set<String> existing = new HashSet<>();
        for (KnowledgeBaseEntity item : knowledgeBaseRepository.findByTenantIdOrderByCreatedAtAsc(tenantId)) {
            if (excludedKbId == null || !excludedKbId.equals(item.getKbId())) {
                existing.add(item.getName().toLowerCase(Locale.ROOT));
            }
        }
        if (!existing.contains(base.toLowerCase(Locale.ROOT))) return base;
        String stem = base.replaceFirst("\\s+\\(\\d+\\)$", "").trim();
        if (stem.isBlank()) stem = "未命名知识库";
        for (int index = 2; ; index++) {
            String candidate = stem + " (" + index + ")";
            if (!existing.contains(candidate.toLowerCase(Locale.ROOT))) return candidate;
        }
    }

    private KnowledgeBaseEntity requireKnowledgeBase(String tenantId, String kbId) {
        return knowledgeBaseRepository.findByTenantIdAndKbId(
                        requireText(tenantId, "tenant id missing"), requireText(kbId, "knowledge base id missing"))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found"));
    }

    private KnowledgeBaseEntity requireOwned(String tenantId, String ownerUserId, String kbId) {
        KnowledgeBaseEntity entity = requireKnowledgeBase(tenantId, kbId);
        if (!requireText(ownerUserId, "owner user id missing").equals(entity.getOwnerUserId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "knowledge base is not owned by current user");
        }
        return entity;
    }

    private KnowledgeBaseDto.KnowledgeBaseView toView(KnowledgeBaseEntity entity) {
        return new KnowledgeBaseDto.KnowledgeBaseView(
                entity.getTenantId(), entity.getKbId(), entity.getName(), entity.getDescription(), readTags(entity.getTagsJson()),
                entity.getVisibility(), entity.getSummary(),
                entity.getStatus(), entity.isFrozen(), entity.getMetadataOwner(), entity.getOwnerUserId(),
                entity.getCreatedAt(), entity.getUpdatedAt()
        );
    }

    private String normalizeDescription(String value) {
        String normalized = value == null ? "" : value.trim();
        return normalized.length() <= 1000 ? normalized : normalized.substring(0, 1000);
    }

    private String userVisibility(String value, String fallback) {
        String target = value == null || value.isBlank() ? fallback : value.trim().toUpperCase(Locale.ROOT);
        Set<String> allowed = "SHARED_ALL".equals(fallback)
                ? Set.of("PRIVATE", "WORKSPACE", "SHARED_ALL")
                : Set.of("PRIVATE", "WORKSPACE");
        if (!allowed.contains(target)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "user visibility must be PRIVATE or WORKSPACE");
        }
        return target;
    }

    private String writeTags(List<String> values) {
        List<String> normalized = values == null ? List.of() : values.stream()
                .map(value -> value == null ? "" : value.trim())
                .filter(value -> !value.isBlank())
                .distinct().limit(10).toList();
        try { return objectMapper.writeValueAsString(normalized); }
        catch (Exception exception) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid knowledge base tags"); }
    }

    private List<String> readTags(String value) {
        try { return objectMapper.readValue(value == null ? "[]" : value, new TypeReference<>() {}); }
        catch (Exception exception) { return List.of(); }
    }

    private KnowledgeBaseDto.BindingView toBindingView(KnowledgeBaseBindingEntity entity) {
        return new KnowledgeBaseDto.BindingView(
                entity.getOwnerTenantId(), entity.getKbId(), entity.getTargetTenantId(), entity.getCreatedBy(),
                entity.getCreatedAt(), entity.getUpdatedAt()
        );
    }

    private String requireText(String value, String message) {
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        return value.trim();
    }
}
