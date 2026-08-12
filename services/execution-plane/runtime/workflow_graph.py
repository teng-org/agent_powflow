from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_NODE_TYPES = {
    "start", "condition.if", "parallel.fork", "parallel.join",
    "llm.generate", "retrieval.search", "skill.call", "tool.http", "tool.mcp", "tool.javascript", "human.approval", "end",
}


@dataclass(frozen=True)
class WorkflowEdge:
    edge_id: str
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class WorkflowGraph:
    entry_node_id: str
    nodes: dict[str, dict]
    outgoing: dict[str, tuple[WorkflowEdge, ...]]
    incoming: dict[str, tuple[WorkflowEdge, ...]]
    fork_joins: dict[str, str]
    join_forks: dict[str, str]
    ordinals: dict[str, int]
    legacy_implicit_edges: bool

    def next_node(self, node: dict, state: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        node_id = str(node["id"])
        node_type = str(node["type"])
        edges = self.outgoing.get(node_id, ())
        if node_type == "end":
            return None, {}
        if node_type == "parallel.fork":
            raise RuntimeError(f"parallel fork {node_id} must be executed by the branch scheduler")
        if node_type == "human.approval":
            decision = str(_lookup(state, "approval.decision") or "").strip().lower()
            selected = next((edge for edge in edges if edge.label == decision), None)
            if selected is None:
                raise RuntimeError(f"human approval node {node_id} has no {decision or 'decision'} branch")
            return selected.target, {
                "approval_decision": decision,
                "selected_edge_id": selected.edge_id,
                "selected_label": selected.label,
            }
        if node_type != "condition.if":
            if len(edges) != 1:
                raise RuntimeError(f"workflow node {node_id} requires exactly one outgoing edge")
            return edges[0].target, {}

        matched = evaluate_condition(node.get("config") or {}, state)
        wanted = "true" if matched else "false"
        selected = next((edge for edge in edges if edge.label == wanted), None)
        if selected is None:
            selected = next((edge for edge in edges if edge.label == "default"), None)
        if selected is None:
            raise RuntimeError(f"condition node {node_id} has no {wanted} or default branch")
        return selected.target, {"condition_result": matched, "selected_edge_id": selected.edge_id, "selected_label": selected.label}


def compile_workflow_graph(dsl: dict) -> WorkflowGraph:
    raw_nodes = dsl.get("nodes") or []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise RuntimeError("workflow requires nodes")
    nodes: dict[str, dict] = {}
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise RuntimeError("workflow node must be an object")
        node_id = str(raw.get("id") or "").strip()
        node_type = str(raw.get("type") or "").strip()
        if not node_id:
            raise RuntimeError("workflow node id is required")
        if node_id in nodes:
            raise RuntimeError(f"duplicate workflow node id: {node_id}")
        if node_type not in SUPPORTED_NODE_TYPES:
            raise RuntimeError(f"unsupported workflow node types: {node_type}")
        nodes[node_id] = raw

    starts = [node_id for node_id, node in nodes.items() if node.get("type") == "start"]
    ends = [node_id for node_id, node in nodes.items() if node.get("type") == "end"]
    if len(starts) != 1 or not ends:
        raise RuntimeError("workflow requires exactly one start and at least one end node")
    entry = str(dsl.get("entry_node_id") or starts[0]).strip()
    if entry != starts[0]:
        raise RuntimeError("workflow entry_node_id must reference the start node")

    raw_edges = dsl.get("edges") or []
    legacy = not raw_edges
    if legacy:
        raw_edges = [
            {"id": f"implicit-{index}", "source_node_id": raw_nodes[index]["id"], "target_node_id": raw_nodes[index + 1]["id"]}
            for index in range(len(raw_nodes) - 1)
        ]
    outgoing: dict[str, list[WorkflowEdge]] = {node_id: [] for node_id in nodes}
    incoming: dict[str, list[WorkflowEdge]] = {node_id: [] for node_id in nodes}
    edge_ids: set[str] = set()
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            raise RuntimeError("workflow edge must be an object")
        edge_id = str(raw.get("id") or f"edge-{index}").strip()
        source = str(raw.get("source_node_id") or "").strip()
        target = str(raw.get("target_node_id") or "").strip()
        label = str(raw.get("condition_label") or "").strip().lower()
        if edge_id in edge_ids:
            raise RuntimeError(f"duplicate workflow edge id: {edge_id}")
        edge_ids.add(edge_id)
        if source not in nodes or target not in nodes:
            raise RuntimeError(f"workflow edge {edge_id} references a missing node")
        edge = WorkflowEdge(edge_id, source, target, label)
        outgoing[source].append(edge)
        incoming[target].append(edge)
        adjacency[source].append(target)
        indegree[target] += 1

    topological_order = _assert_dag(adjacency, indegree, list(nodes))
    _assert_reachable(entry, adjacency, nodes)
    for node_id, node in nodes.items():
        edges = outgoing[node_id]
        node_type = node.get("type")
        if node_type == "end" and edges:
            raise RuntimeError(f"end node {node_id} cannot have outgoing edges")
        if node_type not in {"end", "condition.if", "parallel.fork", "human.approval"} and len(edges) != 1:
            raise RuntimeError(f"workflow node {node_id} requires exactly one outgoing edge")
        if node_type == "condition.if":
            labels = [edge.label for edge in edges]
            if any(label not in {"true", "false", "default"} for label in labels):
                raise RuntimeError(f"condition node {node_id} has an invalid branch label")
            if len(labels) != len(set(labels)):
                raise RuntimeError(f"condition node {node_id} has duplicate branch labels")
            if not (("true" in labels and "false" in labels) or "default" in labels):
                raise RuntimeError(f"condition node {node_id} requires true/false branches or a default branch")
        if node_type == "parallel.fork":
            labels = [edge.label for edge in edges]
            if len(edges) < 2 or len(edges) > 8:
                raise RuntimeError(f"parallel fork {node_id} requires between 2 and 8 branches")
            if any(not label for label in labels):
                raise RuntimeError(f"parallel fork {node_id} requires a non-empty label on every branch")
            if len(labels) != len(set(labels)):
                raise RuntimeError(f"parallel fork {node_id} has duplicate branch labels")
        if node_type == "human.approval":
            labels = [edge.label for edge in edges]
            if len(edges) != 2 or set(labels) != {"approve", "reject"}:
                raise RuntimeError(
                    f"human approval node {node_id} requires exactly approve and reject branches"
                )
            _validate_human_approval_config(node_id, node.get("config"))
        if node_type == "parallel.join" and len(incoming[node_id]) < 2:
            raise RuntimeError(f"parallel join {node_id} requires at least two incoming edges")

    fork_joins, join_forks = _resolve_parallel_pairs(nodes, outgoing)
    return WorkflowGraph(
        entry,
        nodes,
        {key: tuple(value) for key, value in outgoing.items()},
        {key: tuple(value) for key, value in incoming.items()},
        fork_joins,
        join_forks,
        {node_id: index for index, node_id in enumerate(topological_order)},
        legacy,
    )


def evaluate_condition(config: dict, state: dict[str, Any]) -> bool:
    field = str(config.get("field") or "text").strip()
    operator = str(config.get("operator") or "truthy").strip().lower()
    actual = _lookup(state, field)
    expected = config.get("value")
    if operator == "truthy":
        return bool(actual)
    if operator == "falsy":
        return not bool(actual)
    if operator in {"equals", "eq"}:
        return actual == expected
    if operator in {"not_equals", "ne"}:
        return actual != expected
    if operator == "contains":
        if isinstance(actual, dict):
            return expected in actual
        if isinstance(actual, (list, tuple, set, str)):
            return expected in actual
        return False
    if operator == "exists":
        return actual is not None
    if operator == "in":
        return actual in expected if isinstance(expected, (list, tuple, set, str, dict)) else False
    raise RuntimeError(f"unsupported condition operator: {operator}")


def _validate_human_approval_config(node_id: str, raw_config: Any) -> None:
    config = raw_config if isinstance(raw_config, dict) else {}
    title = str(config.get("title") or "").strip()
    if not title or len(title) > 160:
        raise RuntimeError(f"human approval node {node_id} requires a title of at most 160 characters")
    instructions = str(config.get("instructions") or "")
    if len(instructions) > 2000:
        raise RuntimeError(f"human approval node {node_id} instructions exceed 2000 characters")
    roles = config.get("assignee_roles")
    if not isinstance(roles, list) or not roles or len(roles) > 8:
        raise RuntimeError(f"human approval node {node_id} requires between 1 and 8 assignee_roles")
    normalized_roles = [str(role or "").strip().upper() for role in roles]
    if any(not role or len(role) > 64 or not role.replace("_", "").isalnum() for role in normalized_roles):
        raise RuntimeError(f"human approval node {node_id} has an invalid assignee role")
    if len(normalized_roles) != len(set(normalized_roles)):
        raise RuntimeError(f"human approval node {node_id} has duplicate assignee roles")
    timeout_seconds = config.get("timeout_seconds", 86400)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 60 <= timeout_seconds <= 604800:
        raise RuntimeError(f"human approval node {node_id} timeout_seconds must be between 60 and 604800")
    timeout_action = str(config.get("timeout_action") or "reject").strip().lower()
    if timeout_action not in {"approve", "reject"}:
        raise RuntimeError(f"human approval node {node_id} timeout_action must be approve or reject")


def _lookup(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _assert_dag(adjacency: dict[str, list[str]], indegree: dict[str, int], node_order: list[str]) -> tuple[str, ...]:
    remaining = dict(indegree)
    queue = [node_id for node_id in node_order if remaining[node_id] == 0]
    visited: list[str] = []
    while queue:
        current = queue.pop(0)
        visited.append(current)
        for target in adjacency[current]:
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if len(visited) != len(remaining):
        raise RuntimeError("workflow graph contains a cycle")
    return tuple(visited)


def _assert_reachable(entry: str, adjacency: dict[str, list[str]], nodes: dict[str, dict]) -> None:
    reachable: set[str] = set()
    stack = [entry]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(adjacency[current])
    missing = sorted(set(nodes) - reachable)
    if missing:
        raise RuntimeError(f"workflow contains unreachable nodes: {', '.join(missing)}")


def _resolve_parallel_pairs(
    nodes: dict[str, dict], outgoing: dict[str, list[WorkflowEdge]]
) -> tuple[dict[str, str], dict[str, str]]:
    fork_joins: dict[str, str] = {}
    join_forks: dict[str, str] = {}
    for fork_id, fork in nodes.items():
        if fork.get("type") != "parallel.fork":
            continue
        branch_sets: list[set[str]] = []
        branch_joins: list[str] = []
        for edge in outgoing[fork_id]:
            joins, visited = _first_joins(edge.target, nodes, outgoing, set())
            if len(joins) != 1:
                raise RuntimeError(f"parallel fork {fork_id} branch {edge.label} must converge at exactly one join")
            branch_joins.append(next(iter(joins)))
            branch_sets.append(visited)
        if len(set(branch_joins)) != 1:
            raise RuntimeError(f"parallel fork {fork_id} branches must converge at the same join")
        join_id = branch_joins[0]
        for index, current in enumerate(branch_sets):
            for other in branch_sets[index + 1:]:
                overlap = current.intersection(other) - {join_id}
                if overlap:
                    raise RuntimeError(
                        f"parallel fork {fork_id} branches merge before join {join_id}: {', '.join(sorted(overlap))}"
                    )
        if join_id in join_forks:
            raise RuntimeError(f"parallel join {join_id} cannot belong to multiple forks")
        labels = {edge.label for edge in outgoing[fork_id]}
        config = nodes[join_id].get("config") if isinstance(nodes[join_id].get("config"), dict) else {}
        answer_branch = str(config.get("answer_branch") or "").strip().lower()
        if answer_branch not in labels:
            raise RuntimeError(f"parallel join {join_id} answer_branch must match a fork branch label")
        fork_joins[fork_id] = join_id
        join_forks[join_id] = fork_id

    unpaired = sorted(
        node_id for node_id, node in nodes.items()
        if node.get("type") == "parallel.join" and node_id not in join_forks
    )
    if unpaired:
        raise RuntimeError(f"parallel joins are not paired with a fork: {', '.join(unpaired)}")
    return fork_joins, join_forks


def _first_joins(
    node_id: str,
    nodes: dict[str, dict],
    outgoing: dict[str, list[WorkflowEdge]],
    path: set[str],
) -> tuple[set[str], set[str]]:
    if node_id in path:
        raise RuntimeError("workflow graph contains a cycle")
    node_type = str(nodes[node_id].get("type") or "")
    if node_type == "parallel.join":
        return {node_id}, {node_id}
    if node_type == "parallel.fork":
        raise RuntimeError("nested parallel forks are not supported in v1")
    if node_type == "human.approval":
        raise RuntimeError("human approval nodes inside parallel branches are not supported in v1")
    if node_type == "end":
        raise RuntimeError("parallel branch reached an end node before a join")
    edges = outgoing[node_id]
    if not edges:
        raise RuntimeError(f"parallel branch stopped before a join at node {node_id}")
    joins: set[str] = set()
    visited = {node_id}
    next_path = set(path)
    next_path.add(node_id)
    for edge in edges:
        child_joins, child_visited = _first_joins(edge.target, nodes, outgoing, next_path)
        joins.update(child_joins)
        visited.update(child_visited)
    return joins, visited
