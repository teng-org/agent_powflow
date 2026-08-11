package com.agentpowflow.controlplane.canvas;

import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Component
public class DslValidator {
    private final NodeTypeRegistry nodeTypeRegistry;
    private final ConnectionRuleEngine connectionRuleEngine;

    public DslValidator(NodeTypeRegistry nodeTypeRegistry, ConnectionRuleEngine connectionRuleEngine) {
        this.nodeTypeRegistry = nodeTypeRegistry;
        this.connectionRuleEngine = connectionRuleEngine;
    }

    public CanvasModels.ValidationResult validate(CanvasModels.AgentCanvasDsl dsl) {
        List<CanvasModels.ValidationIssue> errors = new ArrayList<>();
        List<CanvasModels.ValidationIssue> warnings = new ArrayList<>();

        List<CanvasModels.Node> nodes = dsl.nodes() == null ? List.of() : dsl.nodes();
        List<CanvasModels.Edge> edges = dsl.edges() == null ? List.of() : dsl.edges();

        Map<String, CanvasModels.Node> nodesById = new HashMap<>();
        for (CanvasModels.Node node : nodes) {
            if (node.id() == null || node.id().isBlank()) {
                errors.add(new CanvasModels.ValidationIssue(
                        null,
                        null,
                        "NODE_ID_REQUIRED",
                        "Node id is required.",
                        "Provide a non-empty id for each node."
                ));
                continue;
            }
            if (nodesById.putIfAbsent(node.id(), node) != null) {
                errors.add(new CanvasModels.ValidationIssue(
                        node.id(),
                        null,
                        "NODE_ID_DUPLICATE",
                        "Duplicate node id found.",
                        "Use unique node ids in nodes[]."
                ));
            }
            if (nodeTypeRegistry.find(node.type()).isEmpty()) {
                errors.add(new CanvasModels.ValidationIssue(
                        node.id(),
                        null,
                        "NODE_TYPE_UNKNOWN",
                        "Node type '" + node.type() + "' is not executable in the current runtime.",
                        "Use a registered node type or implement and register its runtime adapter before publishing."
                ));
            }
        }

        validateEntryNode(dsl, nodes, edges, errors);
        validateNodeConfigs(nodes, errors);
        validateEndNodes(nodesById, edges, errors);
        validateDag(nodesById, edges, errors);
        validateReachability(dsl, nodesById, edges, errors);
        errors.addAll(connectionRuleEngine.validate(nodesById, edges));

        return new CanvasModels.ValidationResult(errors, warnings);
    }

    private void validateEntryNode(
            CanvasModels.AgentCanvasDsl dsl,
            List<CanvasModels.Node> nodes,
            List<CanvasModels.Edge> edges,
            List<CanvasModels.ValidationIssue> errors
    ) {
        List<CanvasModels.Node> startNodes = nodes.stream()
                .filter(node -> "start".equals(node.type()))
                .toList();

        if (!startNodes.isEmpty()) {
            if (startNodes.size() != 1) {
                errors.add(new CanvasModels.ValidationIssue(
                        null,
                        null,
                        "ENTRY_NOT_UNIQUE",
                        "Workflow must contain exactly one start node.",
                        "Keep one start node and remove extra start nodes."
                ));
            }
            if (dsl.entryNodeId() != null && !dsl.entryNodeId().isBlank() &&
                    startNodes.stream().noneMatch(node -> node.id().equals(dsl.entryNodeId()))) {
                errors.add(new CanvasModels.ValidationIssue(
                        dsl.entryNodeId(),
                        null,
                        "ENTRY_NODE_MISMATCH",
                        "entry_node_id does not reference the start node.",
                        "Set entry_node_id to the single start node id."
                ));
            }
            return;
        }

        Map<String, Integer> indegree = new HashMap<>();
        for (CanvasModels.Node node : nodes) {
            indegree.put(node.id(), 0);
        }
        for (CanvasModels.Edge edge : edges) {
            indegree.computeIfPresent(edge.targetNodeId(), (ignored, current) -> current + 1);
        }

        List<String> roots = indegree.entrySet().stream()
                .filter(entry -> entry.getValue() == 0)
                .map(Map.Entry::getKey)
                .toList();

        if (roots.size() != 1) {
            errors.add(new CanvasModels.ValidationIssue(
                    null,
                    null,
                    "ENTRY_NOT_UNIQUE",
                    "Workflow must contain exactly one entry node.",
                    "Ensure exactly one node has no inbound edges, or add a start node."
            ));
        }
    }

    private void validateEndNodes(
            Map<String, CanvasModels.Node> nodesById,
            List<CanvasModels.Edge> edges,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Set<String> endNodeIds = nodesById.values().stream()
                .filter(node -> "end".equals(node.type()))
                .map(CanvasModels.Node::id)
                .collect(Collectors.toSet());

        if (endNodeIds.isEmpty()) {
            errors.add(new CanvasModels.ValidationIssue(
                    null,
                    null,
                    "END_NODE_MISSING",
                    "Workflow must contain at least one end node.",
                    "Add at least one node with type=end."
            ));
            return;
        }

        for (CanvasModels.Edge edge : edges) {
            if (endNodeIds.contains(edge.sourceNodeId())) {
                errors.add(new CanvasModels.ValidationIssue(
                        edge.sourceNodeId(),
                        edge.id(),
                        "END_NODE_HAS_OUTGOING",
                        "end node cannot have outgoing edges.",
                        "Remove outgoing edge from end node."
                ));
            }
        }
    }

    private void validateDag(
            Map<String, CanvasModels.Node> nodesById,
            List<CanvasModels.Edge> edges,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Map<String, List<CanvasModels.Edge>> adjacency = new HashMap<>();
        Map<String, Integer> indegree = new HashMap<>();

        for (String nodeId : nodesById.keySet()) {
            adjacency.put(nodeId, new ArrayList<>());
            indegree.put(nodeId, 0);
        }

        for (CanvasModels.Edge edge : edges) {
            if (!nodesById.containsKey(edge.sourceNodeId()) || !nodesById.containsKey(edge.targetNodeId())) {
                continue;
            }
            adjacency.get(edge.sourceNodeId()).add(edge);
            indegree.compute(edge.targetNodeId(), (ignored, current) -> current == null ? 1 : current + 1);
        }

        ArrayDeque<String> queue = new ArrayDeque<>();
        for (Map.Entry<String, Integer> entry : indegree.entrySet()) {
            if (entry.getValue() == 0) {
                queue.add(entry.getKey());
            }
        }

        int visited = 0;
        while (!queue.isEmpty()) {
            String nodeId = queue.removeFirst();
            visited++;
            for (CanvasModels.Edge edge : adjacency.getOrDefault(nodeId, List.of())) {
                int nextInDegree = indegree.compute(edge.targetNodeId(), (ignored, current) -> (current == null ? 1 : current) - 1);
                if (nextInDegree == 0) {
                    queue.add(edge.targetNodeId());
                }
            }
        }

        if (visited != nodesById.size()) {
            Set<String> cycleNodes = new HashSet<>();
            for (Map.Entry<String, Integer> entry : indegree.entrySet()) {
                if (entry.getValue() > 0) {
                    cycleNodes.add(entry.getKey());
                }
            }
            errors.add(new CanvasModels.ValidationIssue(
                    cycleNodes.stream().findFirst().orElse(null),
                    null,
                    "CYCLE_DETECTED",
                    "v1 does not support cycles. The workflow must be a DAG.",
                    "Remove back edges to make the graph acyclic."
            ));
        }
    }

    private void validateNodeConfigs(
            List<CanvasModels.Node> nodes,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Set<String> conditionOperators = Set.of(
                "truthy", "falsy", "equals", "eq", "not_equals", "ne", "contains", "exists", "in"
        );
        for (CanvasModels.Node node : nodes) {
            Map<String, Object> config = node.config() == null ? Map.of() : node.config();
            validateRetryConfig(node, config, errors);
            if ("condition.if".equals(node.type())) {
                Object field = config.get("field");
                if (field != null && (!(field instanceof String) || ((String) field).isBlank())) {
                    errors.add(configIssue(node.id(), "CONDITION_FIELD_INVALID", "condition field must be a non-empty string."));
                }
                Object operator = config.getOrDefault("operator", "truthy");
                if (!(operator instanceof String) || !conditionOperators.contains(((String) operator).trim().toLowerCase())) {
                    errors.add(configIssue(node.id(), "CONDITION_OPERATOR_INVALID", "condition operator is not supported."));
                }
            }
            if ("llm.generate".equals(node.type())) {
                validateNumberRange(node.id(), config, "temperature", 0, 2, errors);
                validateNumberRange(node.id(), config, "max_tokens", 1, 32768, errors);
                validateNumberRange(node.id(), config, "mock_delay_ms", 0, 5000, errors);
                for (String key : List.of("prompt", "system_prompt", "pool", "task")) {
                    Object value = config.get(key);
                    if (value != null && !(value instanceof String)) {
                        errors.add(configIssue(node.id(), "LLM_CONFIG_INVALID", key + " must be a string."));
                    }
                }
                Object mockFail = config.get("mock_fail");
                if (mockFail != null && !(mockFail instanceof Boolean)) {
                    errors.add(configIssue(node.id(), "LLM_CONFIG_INVALID", "mock_fail must be a boolean."));
                }
            }
            if ("retrieval.search".equals(node.type())) {
                Object kbId = config.get("kb_id");
                if (!(kbId instanceof String) || ((String) kbId).isBlank()) {
                    errors.add(configIssue(node.id(), "RETRIEVAL_CONFIG_INVALID", "kb_id must be a non-empty string."));
                }
                validateNumberRange(node.id(), config, "top_k", 1, 20, "RETRIEVAL_CONFIG_INVALID", errors);
                validateNumberRange(node.id(), config, "candidate_k", 1, 200, "RETRIEVAL_CONFIG_INVALID", errors);
                validateNumberRange(node.id(), config, "max_chunks_per_doc", 1, 20, "RETRIEVAL_CONFIG_INVALID", errors);
                validateNumberRange(node.id(), config, "context_window", 0, 2, "RETRIEVAL_CONFIG_INVALID", errors);
                validateNumberRange(node.id(), config, "rerank_k", 1, 100, "RETRIEVAL_CONFIG_INVALID", errors);
                Object rankMethod = config.get("rank_method");
                if (rankMethod != null && (!(rankMethod instanceof String)
                        || !Set.of("vector", "hybrid", "rrf", "keyword").contains(((String) rankMethod).trim().toLowerCase()))) {
                    errors.add(configIssue(node.id(), "RETRIEVAL_CONFIG_INVALID", "rank_method must be vector, hybrid, rrf, or keyword."));
                }
                for (String key : List.of("rerank_enabled", "rerank_required")) {
                    Object value = config.get(key);
                    if (value != null && !(value instanceof Boolean)) {
                        errors.add(configIssue(node.id(), "RETRIEVAL_CONFIG_INVALID", key + " must be a boolean."));
                    }
                }
                for (String key : List.of("query_field", "output_field")) {
                    Object value = config.get(key);
                    if (value != null && (!(value instanceof String) || ((String) value).isBlank())) {
                        errors.add(configIssue(node.id(), "RETRIEVAL_CONFIG_INVALID", key + " must be a non-empty string."));
                    }
                }
                Object docIds = config.get("doc_ids");
                if (docIds != null && (!(docIds instanceof List<?> list) || list.stream().anyMatch(value -> !(value instanceof String) || ((String) value).isBlank()))) {
                    errors.add(configIssue(node.id(), "RETRIEVAL_CONFIG_INVALID", "doc_ids must be a list of non-empty strings."));
                }
            }
            if ("tool.http".equals(node.type())) {
                Object url = config.get("url");
                if (!(url instanceof String) || ((String) url).isBlank()) {
                    errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "url must be a non-empty string."));
                } else if (!((String) url).startsWith("${")) {
                    try {
                        URI parsed = new URI((String) url);
                        if (!("http".equalsIgnoreCase(parsed.getScheme()) || "https".equalsIgnoreCase(parsed.getScheme()))
                                || parsed.getHost() == null || parsed.getUserInfo() != null || parsed.getFragment() != null) {
                            errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "url must be an HTTP(S) URL without credentials or a fragment."));
                        }
                    } catch (URISyntaxException exception) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "url is not a valid URI."));
                    }
                }
                Object method = config.getOrDefault("method", "GET");
                if (!(method instanceof String) || !Set.of("GET", "POST", "PUT", "PATCH", "DELETE").contains(((String) method).trim().toUpperCase())) {
                    errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "method must be GET, POST, PUT, PATCH, or DELETE."));
                }
                validateNumberRange(node.id(), config, "timeout_seconds", 1, 60, "HTTP_TOOL_CONFIG_INVALID", errors);
                for (String key : List.of("headers", "query", "body")) {
                    Object value = config.get(key);
                    if (value != null && !(value instanceof Map<?, ?>)) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", key + " must be an object."));
                    }
                }
                Object headers = config.get("headers");
                if (headers instanceof Map<?, ?> headerMap) {
                    Set<String> allowedHeaders = Set.of("accept", "content-type", "user-agent", "x-request-id", "x-correlation-id");
                    boolean invalidHeader = headerMap.entrySet().stream().anyMatch(entry ->
                            !(entry.getKey() instanceof String)
                                    || !allowedHeaders.contains(((String) entry.getKey()).trim().toLowerCase())
                                    || !(entry.getValue() instanceof String)
                                    || ((String) entry.getValue()).contains("\r")
                                    || ((String) entry.getValue()).contains("\n")
                    );
                    if (invalidHeader) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "headers contain an unsupported name or value."));
                    }
                }
                Object outputField = config.get("output_field");
                if (outputField != null && (!(outputField instanceof String) || ((String) outputField).isBlank())) {
                    errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "output_field must be a non-empty string."));
                }
                Object auth = config.get("auth");
                if (auth != null && !(auth instanceof Map<?, ?>)) {
                    errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "auth must be an object."));
                } else if (auth instanceof Map<?, ?> authMap) {
                    Object secretRef = authMap.get("secret_ref");
                    if (!(secretRef instanceof String) || ((String) secretRef).isBlank()) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "auth.secret_ref must be a non-empty string."));
                    }
                    Object header = authMap.containsKey("header") ? authMap.get("header") : "Authorization";
                    if (!(header instanceof String) || !Set.of("authorization", "x-api-key").contains(((String) header).trim().toLowerCase())) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "auth.header must be Authorization or X-Api-Key."));
                    }
                    Object prefix = authMap.get("prefix");
                    if (prefix != null && (!(prefix instanceof String) || ((String) prefix).length() > 64
                            || ((String) prefix).contains("\r") || ((String) prefix).contains("\n"))) {
                        errors.add(configIssue(node.id(), "HTTP_TOOL_CONFIG_INVALID", "auth.prefix must be a safe string of at most 64 characters."));
                    }
                }
            }
        }
    }

    private void validateRetryConfig(
            CanvasModels.Node node,
            Map<String, Object> config,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Object retry = config.get("retry");
        if (retry == null) return;
        if (!Set.of("llm.generate", "retrieval.search", "tool.http").contains(node.type())) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry is only supported for LLM, retrieval, and HTTP tool nodes."));
            return;
        }
        if (!(retry instanceof Map<?, ?> retryMap)) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry must be an object."));
            return;
        }
        Set<String> allowed = Set.of("max_attempts", "initial_delay_ms", "backoff_multiplier", "max_delay_ms", "retry_on");
        if (retryMap.keySet().stream().anyMatch(key -> !(key instanceof String) || !allowed.contains(key))) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry contains an unsupported field."));
        }
        Object maxAttempts = retryMap.get("max_attempts");
        if (maxAttempts != null && (!(maxAttempts instanceof Number number)
                || number.doubleValue() != Math.rint(number.doubleValue())
                || number.intValue() < 1 || number.intValue() > 5)) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry.max_attempts must be an integer between 1 and 5."));
        }
        validateRetryNumber(node.id(), retryMap, "initial_delay_ms", 0, 30000, errors);
        validateRetryNumber(node.id(), retryMap, "backoff_multiplier", 1, 4, errors);
        validateRetryNumber(node.id(), retryMap, "max_delay_ms", 0, 60000, errors);
        Object retryOn = retryMap.containsKey("retry_on") ? retryMap.get("retry_on") : "transient";
        if (!(retryOn instanceof String) || !Set.of("transient", "any").contains(((String) retryOn).trim().toLowerCase())) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry.retry_on must be transient or any."));
        }
        if (retryMap.get("initial_delay_ms") instanceof Number initial && retryMap.get("max_delay_ms") instanceof Number maximum
                && maximum.doubleValue() < initial.doubleValue()) {
            errors.add(configIssue(node.id(), "NODE_RETRY_CONFIG_INVALID", "retry.max_delay_ms cannot be less than retry.initial_delay_ms."));
        }
    }

    private void validateRetryNumber(
            String nodeId,
            Map<?, ?> retry,
            String key,
            double min,
            double max,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Object value = retry.get(key);
        if (value != null && (!(value instanceof Number) || ((Number) value).doubleValue() < min || ((Number) value).doubleValue() > max)) {
            errors.add(configIssue(nodeId, "NODE_RETRY_CONFIG_INVALID", "retry." + key + " must be between " + min + " and " + max + "."));
        }
    }

    private void validateNumberRange(
            String nodeId,
            Map<String, Object> config,
            String key,
            double min,
            double max,
            List<CanvasModels.ValidationIssue> errors
    ) {
        validateNumberRange(nodeId, config, key, min, max, "LLM_CONFIG_INVALID", errors);
    }

    private void validateNumberRange(
            String nodeId,
            Map<String, Object> config,
            String key,
            double min,
            double max,
            String errorCode,
            List<CanvasModels.ValidationIssue> errors
    ) {
        Object value = config.get(key);
        if (value == null) {
            return;
        }
        if (!(value instanceof Number number) || number.doubleValue() < min || number.doubleValue() > max) {
            errors.add(configIssue(nodeId, errorCode, key + " must be between " + min + " and " + max + "."));
        }
    }

    private CanvasModels.ValidationIssue configIssue(String nodeId, String code, String message) {
        return new CanvasModels.ValidationIssue(
                nodeId,
                null,
                code,
                message,
                "Correct the node config before validating or publishing the workflow."
        );
    }

    private void validateReachability(
            CanvasModels.AgentCanvasDsl dsl,
            Map<String, CanvasModels.Node> nodesById,
            List<CanvasModels.Edge> edges,
            List<CanvasModels.ValidationIssue> errors
    ) {
        String entry = dsl.entryNodeId();
        if (entry == null || entry.isBlank() || !nodesById.containsKey(entry)) {
            return;
        }
        Map<String, List<String>> adjacency = new HashMap<>();
        for (String nodeId : nodesById.keySet()) {
            adjacency.put(nodeId, new ArrayList<>());
        }
        for (CanvasModels.Edge edge : edges) {
            if (nodesById.containsKey(edge.sourceNodeId()) && nodesById.containsKey(edge.targetNodeId())) {
                adjacency.get(edge.sourceNodeId()).add(edge.targetNodeId());
            }
        }
        Set<String> reachable = new HashSet<>();
        ArrayDeque<String> queue = new ArrayDeque<>();
        queue.add(entry);
        while (!queue.isEmpty()) {
            String current = queue.removeFirst();
            if (!reachable.add(current)) {
                continue;
            }
            queue.addAll(adjacency.getOrDefault(current, List.of()));
        }
        nodesById.keySet().stream()
                .filter(nodeId -> !reachable.contains(nodeId))
                .sorted()
                .forEach(nodeId -> errors.add(new CanvasModels.ValidationIssue(
                        nodeId,
                        null,
                        "NODE_UNREACHABLE",
                        "Node is not reachable from entry_node_id.",
                        "Connect the node to the entry path or remove it from the workflow."
                )));
    }
}
