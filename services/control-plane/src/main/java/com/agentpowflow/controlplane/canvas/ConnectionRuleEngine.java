package com.agentpowflow.controlplane.canvas;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Component
public class ConnectionRuleEngine {
    private final NodeTypeRegistry nodeTypeRegistry;

    public ConnectionRuleEngine(NodeTypeRegistry nodeTypeRegistry) {
        this.nodeTypeRegistry = nodeTypeRegistry;
    }

    public List<CanvasModels.ValidationIssue> validate(
            Map<String, CanvasModels.Node> nodesById,
            List<CanvasModels.Edge> edges
    ) {
        List<CanvasModels.ValidationIssue> issues = new ArrayList<>();
        Map<String, List<CanvasModels.Edge>> outgoing = groupBy(edges, true);
        Map<String, List<CanvasModels.Edge>> incoming = groupBy(edges, false);
        validateSchemas(nodesById, edges, issues);
        validateDegreesAndLabels(nodesById, outgoing, incoming, issues);
        validateStructuredParallel(nodesById, outgoing, issues);
        return issues;
    }

    private void validateSchemas(
            Map<String, CanvasModels.Node> nodesById,
            List<CanvasModels.Edge> edges,
            List<CanvasModels.ValidationIssue> issues
    ) {
        for (CanvasModels.Edge edge : edges) {
            CanvasModels.Node source = nodesById.get(edge.sourceNodeId());
            CanvasModels.Node target = nodesById.get(edge.targetNodeId());
            if (source == null || target == null) {
                issues.add(issue(null, edge.id(), "EDGE_NODE_NOT_FOUND", "Edge references a missing node.",
                        "Make sure source_node_id and target_node_id both exist in nodes[]."));
                continue;
            }
            var sourceDef = nodeTypeRegistry.find(source.type());
            var targetDef = nodeTypeRegistry.find(target.type());
            if (sourceDef.isEmpty() || targetDef.isEmpty()) continue;
            Map<String, String> output = sourceDef.get().outputSchema().fields();
            for (Map.Entry<String, String> required : targetDef.get().inputSchema().fields().entrySet()) {
                String outputType = output.get(required.getKey());
                if (outputType == null) {
                    issues.add(issue(target.id(), edge.id(), "SCHEMA_FIELD_MISSING",
                            "Missing required field '" + required.getKey() + "' from upstream output.",
                            "Add field '" + required.getKey() + "' to node type '" + source.type() + "' output or insert a transform node."));
                } else if (!outputType.equals(required.getValue())) {
                    issues.add(issue(target.id(), edge.id(), "SCHEMA_TYPE_MISMATCH",
                            "Field '" + required.getKey() + "' expects type '" + required.getValue() + "' but got '" + outputType + "'.",
                            "Adjust upstream output field type to match downstream input."));
                }
            }
        }
    }

    private void validateDegreesAndLabels(
            Map<String, CanvasModels.Node> nodesById,
            Map<String, List<CanvasModels.Edge>> outgoing,
            Map<String, List<CanvasModels.Edge>> incoming,
            List<CanvasModels.ValidationIssue> issues
    ) {
        for (CanvasModels.Node node : nodesById.values()) {
            List<CanvasModels.Edge> next = outgoing.getOrDefault(node.id(), List.of());
            if ("condition.if".equals(node.type())) {
                List<String> labels = normalizedLabels(next);
                boolean duplicate = labels.size() != new HashSet<>(labels).size();
                boolean invalid = labels.stream().anyMatch(label -> !Set.of("true", "false", "default").contains(label));
                if (duplicate || invalid) {
                    issues.add(issue(node.id(), null, "CONDITION_BRANCH_INVALID",
                            "condition.if branch labels must be unique true, false, or default values.",
                            "Remove duplicate labels and use only true, false, or default."));
                }
                boolean hasTrue = labels.contains("true");
                boolean hasFalse = labels.contains("false");
                boolean hasDefault = labels.contains("default");
                if (!(hasTrue && hasFalse) && !hasDefault) {
                    issues.add(issue(node.id(), null, "CONDITION_BRANCH_MISSING",
                            "condition.if node must provide true/false branches, or at least a default branch.",
                            "Add edges with condition_label=true and condition_label=false, or add condition_label=default."));
                }
                continue;
            }
            if ("parallel.fork".equals(node.type())) {
                List<String> labels = normalizedLabels(next);
                if (next.size() < 2 || next.size() > 8) {
                    issues.add(issue(node.id(), null, "PARALLEL_BRANCH_COUNT_INVALID",
                            "parallel.fork requires between 2 and 8 outgoing branches.",
                            "Connect 2 to 8 labeled branches to the fork."));
                }
                if (labels.stream().anyMatch(String::isBlank) || labels.size() != new HashSet<>(labels).size()) {
                    issues.add(issue(node.id(), null, "PARALLEL_BRANCH_LABEL_INVALID",
                            "parallel.fork branch labels must be non-empty and unique.",
                            "Assign a stable unique condition_label to every fork edge."));
                }
                continue;
            }
            if ("parallel.join".equals(node.type())) {
                if (incoming.getOrDefault(node.id(), List.of()).size() < 2) {
                    issues.add(issue(node.id(), null, "PARALLEL_JOIN_INPUT_INVALID",
                            "parallel.join requires at least two incoming edges.",
                            "Connect all fork branches to the same join."));
                }
                if (next.size() != 1) {
                    issues.add(issue(node.id(), null, "PARALLEL_JOIN_OUTPUT_INVALID",
                            "parallel.join requires exactly one outgoing edge.",
                            "Connect the join to exactly one downstream node."));
                }
                continue;
            }
            if ("human.approval".equals(node.type())) {
                List<String> labels = normalizedLabels(next);
                if (next.size() != 2 || !Set.copyOf(labels).equals(Set.of("approve", "reject"))) {
                    issues.add(issue(node.id(), null, "HUMAN_APPROVAL_BRANCH_INVALID",
                            "human.approval requires exactly one approve and one reject branch.",
                            "Add two outgoing edges labeled approve and reject."));
                }
                validateApprovalConfig(node, issues);
                continue;
            }
            if (!"end".equals(node.type()) && next.size() != 1) {
                issues.add(issue(node.id(), null, "NODE_OUTGOING_AMBIGUOUS",
                        "Only condition.if and parallel.fork may have multiple outgoing edges.",
                        "Keep exactly one outgoing edge or use an explicit condition/fork node."));
            }
        }
    }

    private void validateStructuredParallel(
            Map<String, CanvasModels.Node> nodesById,
            Map<String, List<CanvasModels.Edge>> outgoing,
            List<CanvasModels.ValidationIssue> issues
    ) {
        Set<String> pairedJoins = new HashSet<>();
        for (CanvasModels.Node fork : nodesById.values()) {
            if (!"parallel.fork".equals(fork.type())) continue;
            List<CanvasModels.Edge> branches = outgoing.getOrDefault(fork.id(), List.of());
            if (branches.size() < 2 || branches.stream().anyMatch(edge -> normalize(edge.conditionLabel()).isBlank())) continue;
            List<Traversal> traversals = new ArrayList<>();
            boolean invalid = false;
            for (CanvasModels.Edge branch : branches) {
                Traversal traversal = firstJoins(branch.targetNodeId(), nodesById, outgoing, new HashSet<>());
                traversals.add(traversal);
                if (traversal.error() != null || traversal.joins().size() != 1) invalid = true;
            }
            if (invalid) {
                issues.add(issue(fork.id(), null, "PARALLEL_JOIN_MISSING",
                        "Every parallel branch must reach exactly one structured join before an end or nested fork.",
                        "Converge all branches at one parallel.join; nested forks are not supported in v1."));
                continue;
            }
            Set<String> joinIds = traversals.stream().flatMap(item -> item.joins().stream()).collect(Collectors.toSet());
            if (joinIds.size() != 1) {
                issues.add(issue(fork.id(), null, "PARALLEL_JOIN_MISMATCH",
                        "All branches from a parallel.fork must converge at the same join.",
                        "Route every branch to one shared parallel.join."));
                continue;
            }
            String joinId = joinIds.iterator().next();
            for (int i = 0; i < traversals.size(); i++) {
                for (int j = i + 1; j < traversals.size(); j++) {
                    Set<String> overlap = new HashSet<>(traversals.get(i).visited());
                    overlap.retainAll(traversals.get(j).visited());
                    overlap.remove(joinId);
                    if (!overlap.isEmpty()) {
                        issues.add(issue(fork.id(), null, "PARALLEL_EARLY_MERGE",
                                "Parallel branches cannot merge before their declared join.",
                                "Keep branch paths disjoint until parallel.join."));
                        invalid = true;
                    }
                }
            }
            if (invalid) continue;
            if (!pairedJoins.add(joinId)) {
                issues.add(issue(joinId, null, "PARALLEL_JOIN_REUSED",
                        "A parallel.join cannot belong to more than one fork.",
                        "Use a dedicated join for each fork."));
                continue;
            }
            CanvasModels.Node join = nodesById.get(joinId);
            Object answerBranch = join.config() == null ? null : join.config().get("answer_branch");
            Set<String> labels = branches.stream().map(edge -> normalize(edge.conditionLabel())).collect(Collectors.toSet());
            if (!(answerBranch instanceof String) || !labels.contains(normalize((String) answerBranch))) {
                issues.add(issue(joinId, null, "PARALLEL_ANSWER_BRANCH_INVALID",
                        "parallel.join answer_branch must match one fork branch label.",
                        "Choose the deterministic branch whose answer/text becomes the downstream state."));
            }
        }
        nodesById.values().stream()
                .filter(node -> "parallel.join".equals(node.type()) && !pairedJoins.contains(node.id()))
                .forEach(node -> issues.add(issue(node.id(), null, "PARALLEL_JOIN_UNPAIRED",
                        "parallel.join is not paired with a structured fork.",
                        "Connect it as the common convergence point of one parallel.fork.")));
    }

    private Traversal firstJoins(
            String nodeId,
            Map<String, CanvasModels.Node> nodesById,
            Map<String, List<CanvasModels.Edge>> outgoing,
            Set<String> path
    ) {
        if (!path.add(nodeId)) return new Traversal(Set.of(), Set.of(nodeId), "cycle");
        CanvasModels.Node node = nodesById.get(nodeId);
        if (node == null) return new Traversal(Set.of(), Set.of(), "missing");
        if ("parallel.join".equals(node.type())) return new Traversal(Set.of(nodeId), Set.of(nodeId), null);
        if ("parallel.fork".equals(node.type())) return new Traversal(Set.of(), Set.of(nodeId), "nested");
        if ("human.approval".equals(node.type())) return new Traversal(Set.of(), Set.of(nodeId), "human-approval");
        if ("end".equals(node.type())) return new Traversal(Set.of(), Set.of(nodeId), "end");
        List<CanvasModels.Edge> next = outgoing.getOrDefault(nodeId, List.of());
        if (next.isEmpty()) return new Traversal(Set.of(), Set.of(nodeId), "stopped");
        Set<String> joins = new HashSet<>();
        Set<String> visited = new HashSet<>();
        visited.add(nodeId);
        String error = null;
        for (CanvasModels.Edge edge : next) {
            Traversal child = firstJoins(edge.targetNodeId(), nodesById, outgoing, new HashSet<>(path));
            joins.addAll(child.joins());
            visited.addAll(child.visited());
            if (child.error() != null) error = child.error();
        }
        return new Traversal(joins, visited, error);
    }

    private List<String> normalizedLabels(List<CanvasModels.Edge> edges) {
        return edges.stream().map(edge -> normalize(edge.conditionLabel())).toList();
    }

    private void validateApprovalConfig(CanvasModels.Node node, List<CanvasModels.ValidationIssue> issues) {
        Map<String, Object> config = node.config() == null ? Map.of() : node.config();
        String title = config.get("title") instanceof String value ? value.trim() : "";
        Object instructions = config.get("instructions");
        Object rawRoles = config.get("assignee_roles");
        Object rawTimeout = config.getOrDefault("timeout_seconds", 86400);
        String timeoutAction = config.get("timeout_action") instanceof String value
                ? value.trim().toLowerCase() : "reject";
        boolean rolesValid = rawRoles instanceof List<?> roles
                && !roles.isEmpty() && roles.size() <= 8
                && roles.stream().allMatch(role -> role instanceof String value
                        && value.matches("[A-Za-z0-9_]{1,64}"))
                && ((List<?>) rawRoles).stream().map(Object::toString).map(String::toUpperCase).distinct().count()
                        == ((List<?>) rawRoles).size();
        boolean timeoutValid = rawTimeout instanceof Number number
                && number.longValue() >= 60 && number.longValue() <= 604800
                && number.doubleValue() == number.longValue();
        if (title.isBlank() || title.length() > 160
                || (instructions != null && (!(instructions instanceof String) || instructions.toString().length() > 2000))
                || !rolesValid || !timeoutValid || !Set.of("approve", "reject").contains(timeoutAction)) {
            issues.add(issue(node.id(), null, "HUMAN_APPROVAL_CONFIG_INVALID",
                    "human.approval requires a title, 1–8 assignee roles, a bounded timeout, and an approve/reject timeout action.",
                    "Set title, assignee_roles, timeout_seconds 60–604800, and timeout_action approve or reject."));
        }
    }

    private String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase();
    }

    private Map<String, List<CanvasModels.Edge>> groupBy(List<CanvasModels.Edge> edges, boolean source) {
        Map<String, List<CanvasModels.Edge>> grouped = new HashMap<>();
        for (CanvasModels.Edge edge : edges) {
            String key = source ? edge.sourceNodeId() : edge.targetNodeId();
            grouped.computeIfAbsent(key, ignored -> new ArrayList<>()).add(edge);
        }
        return grouped;
    }

    private CanvasModels.ValidationIssue issue(String nodeId, String edgeId, String code, String message, String suggestion) {
        return new CanvasModels.ValidationIssue(nodeId, edgeId, code, message, suggestion);
    }

    private record Traversal(Set<String> joins, Set<String> visited, String error) {}
}
