package com.agentpowflow.controlplane.canvas;

import com.agentpowflow.controlplane.web.dto.CanvasValidationDto;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
public class CanvasDslMapper {
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;

    public CanvasDslMapper(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public CanvasModels.AgentCanvasDsl fromRequest(CanvasValidationDto.ValidateRequest request) {
        return new CanvasModels.AgentCanvasDsl(
                request.entry_node_id(),
                request.nodes().stream()
                        .map(node -> new CanvasModels.Node(node.id(), node.type(), node.config()))
                        .toList(),
                request.edges().stream()
                        .map(edge -> new CanvasModels.Edge(edge.id(), edge.source_node_id(), edge.target_node_id(), edge.condition_label()))
                        .toList()
        );
    }

    public CanvasModels.AgentCanvasDsl fromJson(JsonNode dsl) {
        if (dsl == null || !dsl.isObject()) {
            return new CanvasModels.AgentCanvasDsl(null, List.of(), List.of());
        }
        List<CanvasModels.Node> nodes = new ArrayList<>();
        JsonNode nodesNode = dsl.path("nodes");
        if (nodesNode.isArray()) {
            for (JsonNode node : nodesNode) {
                Map<String, Object> config = node.path("config").isObject()
                        ? objectMapper.convertValue(node.path("config"), MAP_TYPE)
                        : Map.of();
                nodes.add(new CanvasModels.Node(text(node, "id"), text(node, "type"), config));
            }
        }
        List<CanvasModels.Edge> edges = new ArrayList<>();
        JsonNode edgesNode = dsl.path("edges");
        if (edgesNode.isArray()) {
            for (JsonNode edge : edgesNode) {
                edges.add(new CanvasModels.Edge(
                        text(edge, "id"),
                        text(edge, "source_node_id"),
                        text(edge, "target_node_id"),
                        text(edge, "condition_label")
                ));
            }
        }
        return new CanvasModels.AgentCanvasDsl(text(dsl, "entry_node_id"), nodes, edges);
    }

    private String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }
}
