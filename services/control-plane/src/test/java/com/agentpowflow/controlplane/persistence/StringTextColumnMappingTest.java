package com.agentpowflow.controlplane.persistence;

import com.agentpowflow.controlplane.integrations.McpConnectionEntity;
import com.agentpowflow.controlplane.integrations.SkillEntity;
import com.agentpowflow.controlplane.integrations.SkillVersionEntity;
import com.agentpowflow.controlplane.orchestration.WorkflowEntity;
import com.agentpowflow.controlplane.orchestration.WorkflowVersionEntity;
import com.agentpowflow.controlplane.product.AgentEntity;
import com.agentpowflow.controlplane.runs.MessageEntity;
import com.agentpowflow.controlplane.runs.RunEntity;
import com.agentpowflow.controlplane.runs.RunEventEntity;
import com.agentpowflow.controlplane.runs.RunNodeAttemptEntity;
import com.agentpowflow.controlplane.runs.RunNodeExecutionEntity;
import jakarta.persistence.Lob;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class StringTextColumnMappingTest {

    @Test
    void textBackedStringFieldsMustNotUsePostgresLargeObjects() {
        var entityTypes = List.of(
                McpConnectionEntity.class,
                SkillEntity.class,
                SkillVersionEntity.class,
                WorkflowEntity.class,
                WorkflowVersionEntity.class,
                AgentEntity.class,
                MessageEntity.class,
                RunEntity.class,
                RunEventEntity.class,
                RunNodeAttemptEntity.class,
                RunNodeExecutionEntity.class
        );

        var invalidFields = entityTypes.stream()
                .flatMap(type -> List.of(type.getDeclaredFields()).stream())
                .filter(field -> field.getType() == String.class)
                .filter(field -> field.isAnnotationPresent(Lob.class))
                .map(field -> field.getDeclaringClass().getSimpleName() + "." + field.getName())
                .toList();

        assertThat(invalidFields).isEmpty();
    }
}
