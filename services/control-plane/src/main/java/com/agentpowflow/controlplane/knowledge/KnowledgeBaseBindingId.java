package com.agentpowflow.controlplane.knowledge;

import java.io.Serializable;
import java.util.Objects;

public class KnowledgeBaseBindingId implements Serializable {
    private String ownerTenantId;
    private String kbId;
    private String targetTenantId;

    public KnowledgeBaseBindingId() {}

    public KnowledgeBaseBindingId(String ownerTenantId, String kbId, String targetTenantId) {
        this.ownerTenantId = ownerTenantId;
        this.kbId = kbId;
        this.targetTenantId = targetTenantId;
    }

    @Override
    public boolean equals(Object value) {
        if (this == value) return true;
        if (!(value instanceof KnowledgeBaseBindingId other)) return false;
        return Objects.equals(ownerTenantId, other.ownerTenantId)
                && Objects.equals(kbId, other.kbId)
                && Objects.equals(targetTenantId, other.targetTenantId);
    }

    @Override
    public int hashCode() { return Objects.hash(ownerTenantId, kbId, targetTenantId); }
}
