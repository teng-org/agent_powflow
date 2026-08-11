package com.agentpowflow.controlplane.canvas;

import java.util.List;
import java.util.Map;

public final class CanvasModels {
    private CanvasModels() {
    }

    public record AgentCanvasDsl(
            String entryNodeId,
            List<Node> nodes,
            List<Edge> edges
    ) {
    }

    public record Node(
            String id,
            String type,
            Map<String, Object> config
    ) {
    }

    public record Edge(
            String id,
            String sourceNodeId,
            String targetNodeId,
            String conditionLabel
    ) {
    }

    public record Schema(
            Map<String, String> fields
    ) {
    }

    public record ValidationIssue(
            String nodeId,
            String edgeId,
            String code,
            String message,
            String suggestion
    ) {
    }

    public record ValidationResult(
            List<ValidationIssue> errors,
            List<ValidationIssue> warnings
    ) {
        public boolean valid() {
            return errors == null || errors.isEmpty();
        }
    }
}
