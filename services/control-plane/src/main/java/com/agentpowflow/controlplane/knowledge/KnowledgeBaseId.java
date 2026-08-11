package com.agentpowflow.controlplane.knowledge;

import java.io.Serializable;
import java.util.Objects;

public class KnowledgeBaseId implements Serializable {
    private String tenantId;
    private String kbId;

    public KnowledgeBaseId() {}

    public KnowledgeBaseId(String tenantId, String kbId) {
        this.tenantId = tenantId;
        this.kbId = kbId;
    }

    @Override
    public boolean equals(Object value) {
        if (this == value) return true;
        if (!(value instanceof KnowledgeBaseId other)) return false;
        return Objects.equals(tenantId, other.tenantId) && Objects.equals(kbId, other.kbId);
    }

    @Override
    public int hashCode() {
        return Objects.hash(tenantId, kbId);
    }
}
