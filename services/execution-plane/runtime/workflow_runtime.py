import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from runtime.node_adapters import NodeExecutionContext, node_adapter_registry
from runtime.node_retry import NodeRetryPolicy
from runtime.workflow_graph import compile_workflow_graph


class WorkflowSuspended(RuntimeError):
    """Durable hand-off requested by a human approval node."""

    def __init__(self, *, node: dict, checkpoint: dict, progress: int) -> None:
        super().__init__(f"workflow is waiting for approval at node {node.get('id')}")
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        self.node_id = str(node.get("id") or "")
        self.node_type = str(node.get("type") or "human.approval")
        self.title = str(config.get("title") or "").strip()
        self.instructions = str(config.get("instructions") or "")
        self.assignee_roles = [str(role).strip().upper() for role in config.get("assignee_roles", [])]
        self.timeout_seconds = int(config.get("timeout_seconds", 86400))
        self.timeout_action = str(config.get("timeout_action") or "reject").strip().lower()
        self.checkpoint = checkpoint
        self.progress = int(progress)


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise RuntimeError("TASK_CANCELLED")


def _question(payload: dict) -> str:
    input_payload = payload.get("input_payload") or {}
    question = str(input_payload.get("text") or input_payload.get("question") or "").strip()
    return question or json.dumps(input_payload, ensure_ascii=False, sort_keys=True)


@dataclass
class _ExecutionStats:
    provider_calls: int = 0
    provider_cache_hits: int = 0
    tool_calls: int = 0
    tool_cache_hits: int = 0
    node_attempt_count: int = 0
    node_retry_count: int = 0
    parallel_fork_count: int = 0
    parallel_branch_count: int = 0
    parallel_max_concurrency: int = 0

    def add_result(self, result) -> None:
        self.provider_calls += int(result.provider_called)
        self.provider_cache_hits += int(result.provider_cache_hit)
        self.tool_calls += int(result.tool_called)
        self.tool_cache_hits += int(result.tool_cache_hit)

    def merge(self, other: "_ExecutionStats") -> None:
        self.provider_calls += other.provider_calls
        self.provider_cache_hits += other.provider_cache_hits
        self.tool_calls += other.tool_calls
        self.tool_cache_hits += other.tool_cache_hits
        self.node_attempt_count += other.node_attempt_count
        self.node_retry_count += other.node_retry_count
        self.parallel_fork_count += other.parallel_fork_count
        self.parallel_branch_count += other.parallel_branch_count
        self.parallel_max_concurrency = max(self.parallel_max_concurrency, other.parallel_max_concurrency)

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Any) -> "_ExecutionStats":
        safe = value if isinstance(value, dict) else {}
        return cls(**{name: max(0, int(safe.get(name) or 0)) for name in cls.__dataclass_fields__})


@dataclass
class _PathResult:
    state: dict[str, Any]
    executed: list[str]
    providers: list[dict | str]
    citations: list[dict]
    stats: _ExecutionStats
    primary_provider: dict | str | None = None
    join_details: dict[str, Any] = field(default_factory=dict)


class _NodeEventEmitter:
    def __init__(self, callback, ordinals: dict[str, int], total: int) -> None:
        self.callback = callback
        self.ordinals = ordinals
        self.total = total
        self.progress = 0
        self.lock = threading.Lock()

    def emit(self, event_type: str, node: dict, details: dict) -> None:
        if self.callback is None:
            return
        node_id = str(node.get("id") or "")
        ordinal = self.ordinals[node_id]
        completed = event_type in {"NODE_COMPLETED", "NODE_FAILED", "NODE_WAITING_APPROVAL"}
        numerator = ordinal + (1 if completed else 0)
        candidate = min(90, 10 + int(numerator * 80 / max(1, self.total)))
        with self.lock:
            self.progress = max(self.progress, candidate)
            self.callback(
                {
                    "event_type": event_type,
                    "node_id": node_id,
                    "node_type": str(node.get("type") or ""),
                    "ordinal": ordinal,
                    "progress": self.progress,
                    "details": details,
                }
            )


def execute_workflow(
    payload: dict,
    should_cancel: Callable[[], bool] | None = None,
    invocation_store=None,
    gateway=None,
    on_node_event: Callable[[dict], None] | None = None,
    retrieval_search=None,
    kb_access_resolver=None,
    http_executor=None,
    secret_resolver=None,
) -> dict:
    provider_mode = os.getenv("WORKFLOW_PROVIDER_MODE", "mock").strip().lower()
    if provider_mode not in {"mock", "api_strict"}:
        raise RuntimeError(f"unsupported workflow provider mode: {provider_mode}")

    graph = compile_workflow_graph(payload.get("workflow_dsl") or {})
    started = time.perf_counter()
    original_question = _question(payload)
    input_payload = payload.get("input_payload") or {}
    approval_resume = payload.get("approval_resume") if isinstance(payload.get("approval_resume"), dict) else None
    resume_checkpoint = approval_resume.get("checkpoint") if approval_resume else None
    if approval_resume and not isinstance(resume_checkpoint, dict):
        raise RuntimeError("approval resume payload requires a checkpoint")
    if resume_checkpoint:
        checkpoint_state = resume_checkpoint.get("state")
        if not isinstance(checkpoint_state, dict):
            raise RuntimeError("approval checkpoint state must be an object")
        initial_state = dict(checkpoint_state)
    else:
        initial_state = dict(input_payload) if isinstance(input_payload, dict) else {"input": input_payload}
        initial_state.setdefault("text", original_question)
    max_steps = len(graph.nodes)
    abort_parallel = threading.Event()
    event_emitter = _NodeEventEmitter(on_node_event, graph.ordinals, max_steps)
    if resume_checkpoint:
        event_emitter.progress = max(0, min(90, int(resume_checkpoint.get("progress") or 0)))
    resume_node_id = str((approval_resume or {}).get("node_id") or "").strip()
    resume_decision = str((approval_resume or {}).get("decision") or "").strip().lower()
    if approval_resume:
        if resume_node_id not in graph.nodes or graph.nodes[resume_node_id].get("type") != "human.approval":
            raise RuntimeError("approval resume node does not exist or is not a human approval node")
        if resume_decision not in {"approve", "reject"}:
            raise RuntimeError("approval resume decision must be approve or reject")

    def execution_cancelled() -> bool:
        return abort_parallel.is_set() or bool(should_cancel and should_cancel())

    def execute_node(node: dict, node_state: dict[str, Any], branch_label: str | None):
        retry_policy = NodeRetryPolicy.from_node(node)
        started_details: dict[str, Any] = {"max_attempts": retry_policy.max_attempts}
        if branch_label:
            started_details["parallel_branch"] = branch_label
        event_emitter.emit("NODE_STARTED", node, started_details)
        context = NodeExecutionContext(
            payload=payload,
            state=node_state,
            provider_mode=provider_mode,
            should_cancel=execution_cancelled,
            invocation_store=invocation_store,
            gateway=gateway,
            retrieval_search=retrieval_search,
            kb_access_resolver=kb_access_resolver,
            http_executor=http_executor,
            secret_resolver=secret_resolver,
        )
        attempt_no = 0
        stats = _ExecutionStats()
        while True:
            attempt_no += 1
            stats.node_attempt_count += 1
            attempt_started = time.perf_counter()
            attempt_details: dict[str, Any] = {
                "attempt_no": attempt_no,
                "max_attempts": retry_policy.max_attempts,
            }
            if branch_label:
                attempt_details["parallel_branch"] = branch_label
            if retry_policy.max_attempts > 1:
                event_emitter.emit("NODE_ATTEMPT_STARTED", node, attempt_details)
            try:
                result = node_adapter_registry.execute(node, context)
            except Exception as exc:
                retryable = retry_policy.should_retry(exc, attempt_no)
                delay_ms = retry_policy.delay_ms(attempt_no) if retryable else 0
                failure_details = {
                    **attempt_details,
                    "duration_ms": max(0, int((time.perf_counter() - attempt_started) * 1000)),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "retryable": retryable,
                    "will_retry": retryable,
                    "retry_delay_ms": delay_ms,
                }
                if retry_policy.max_attempts > 1:
                    event_emitter.emit("NODE_ATTEMPT_FAILED", node, failure_details)
                if retryable:
                    stats.node_retry_count += 1
                    _interruptible_sleep(delay_ms, execution_cancelled)
                    continue
                event_emitter.emit("NODE_FAILED", node, failure_details)
                raise
            if retry_policy.max_attempts > 1:
                event_emitter.emit(
                    "NODE_ATTEMPT_SUCCEEDED",
                    node,
                    {
                        **attempt_details,
                        "duration_ms": max(0, int((time.perf_counter() - attempt_started) * 1000)),
                    },
                )
            stats.add_result(result)
            return result, attempt_no, stats

    def run_parallel(edges, join_id: str, base_state: dict[str, Any]) -> _PathResult:
        branch_results: dict[str, _PathResult] = {}

        def run_branch(edge):
            try:
                return run_path(edge.target, dict(base_state), stop_before=join_id, branch_label=edge.label)
            except Exception:
                abort_parallel.set()
                raise

        with ThreadPoolExecutor(max_workers=len(edges), thread_name_prefix="workflow-branch") as pool:
            futures = {pool.submit(run_branch, edge): edge for edge in edges}
            failures: list[tuple[str, Exception]] = []
            for future in as_completed(futures):
                edge = futures[future]
                try:
                    branch_results[edge.label] = future.result()
                except Exception as exc:
                    failures.append((edge.label, exc))

        if failures:
            if should_cancel and should_cancel():
                raise RuntimeError("TASK_CANCELLED")
            by_label = {label: failure for label, failure in failures}
            deterministic = [
                (edge.label, by_label[edge.label]) for edge in edges
                if edge.label in by_label and str(by_label[edge.label]) != "TASK_CANCELLED"
            ]
            label, failure = deterministic[0] if deterministic else next(
                (edge.label, by_label[edge.label]) for edge in edges if edge.label in by_label
            )
            raise RuntimeError(f"parallel branch {label} failed: {failure}") from failure

        ordered = [(edge.label, branch_results[edge.label]) for edge in edges]
        deltas = {
            label: {key: value for key, value in result.state.items() if key not in base_state or base_state[key] != value}
            for label, result in ordered
        }
        join_config = graph.nodes[join_id].get("config") or {}
        answer_branch = str(join_config.get("answer_branch") or "").strip().lower()
        selected = branch_results[answer_branch]
        merged = _PathResult(
            state={**selected.state, "branches": deltas},
            executed=[],
            providers=[],
            citations=[],
            stats=_ExecutionStats(parallel_fork_count=1, parallel_branch_count=len(edges), parallel_max_concurrency=len(edges)),
            primary_provider=selected.primary_provider,
            join_details={
                "answer_branch": answer_branch,
                "completed_branches": [label for label, _ in ordered],
                "branch_output_fields": {label: sorted(delta) for label, delta in deltas.items()},
            },
        )
        for _, result in ordered:
            merged.executed.extend(result.executed)
            merged.providers.extend(result.providers)
            merged.citations.extend(result.citations)
            merged.stats.merge(result.stats)
        return merged

    def run_path(
        current_id: str | None,
        path_state: dict[str, Any],
        stop_before: str | None = None,
        branch_label: str | None = None,
        seed: _PathResult | None = None,
    ) -> _PathResult:
        executed: list[str] = list(seed.executed) if seed else []
        providers: list[dict | str] = list(seed.providers) if seed else []
        citations: list[dict] = list(seed.citations) if seed else []
        stats = seed.stats if seed else _ExecutionStats()
        primary_provider: dict | str | None = seed.primary_provider if seed else None
        seen: set[str] = set()
        pending_join_details: dict[str, dict[str, Any]] = {}
        while current_id is not None and current_id != stop_before:
            _raise_if_cancelled(execution_cancelled)
            if current_id in seen or len(executed) >= max_steps:
                raise RuntimeError(f"workflow execution revisited node: {current_id}")
            seen.add(current_id)
            node = graph.nodes[current_id]
            node_type = str(node.get("type") or "")
            if node_type == "human.approval":
                if branch_label is not None:
                    raise RuntimeError("human approval nodes inside parallel branches are not supported in v1")
                started_details = {"max_attempts": 1}
                event_emitter.emit("NODE_STARTED", node, started_details)
                if current_id != resume_node_id:
                    waiting_details = {
                        "title": str((node.get("config") or {}).get("title") or ""),
                        "assignee_roles": list((node.get("config") or {}).get("assignee_roles") or []),
                        "timeout_seconds": int((node.get("config") or {}).get("timeout_seconds", 86400)),
                        "timeout_action": str((node.get("config") or {}).get("timeout_action") or "reject").lower(),
                    }
                    event_emitter.emit("NODE_WAITING_APPROVAL", node, waiting_details)
                    checkpoint = {
                        "version": 1,
                        "node_id": current_id,
                        "ordinal": graph.ordinals[current_id],
                        "progress": event_emitter.progress,
                        "state": dict(path_state),
                        "executed_node_ids": list(executed),
                        "providers": list(providers),
                        "citations": list(citations),
                        "primary_provider": primary_provider,
                        "stats": stats.to_dict(),
                    }
                    raise WorkflowSuspended(node=node, checkpoint=checkpoint, progress=event_emitter.progress)
                approval_state = {
                    "decision": resume_decision,
                    "approval_id": str((approval_resume or {}).get("approval_id") or ""),
                    "decided_by": str((approval_resume or {}).get("decided_by") or ""),
                    "decision_reason": str((approval_resume or {}).get("decision_reason") or ""),
                    "decided_at": str((approval_resume or {}).get("decided_at") or ""),
                    "timed_out": bool((approval_resume or {}).get("timed_out")),
                }
                path_state["approval"] = approval_state
                stats.node_attempt_count += 1
                next_id, branch_details = graph.next_node(node, path_state)
                executed.append(current_id)
                event_emitter.emit("NODE_COMPLETED", node, {**branch_details, **approval_state, "attempt_count": 1})
                current_id = next_id
                continue
            result, attempt_no, node_stats = execute_node(node, path_state, branch_label)
            path_state.update(result.state_updates)
            stats.merge(node_stats)
            citations.extend(result.citations)
            if result.provider is not None:
                providers.append(result.provider)
                primary_provider = result.provider

            details = dict(result.details)
            details["attempt_count"] = attempt_no
            if branch_label:
                details["parallel_branch"] = branch_label

            if node_type == "parallel.fork":
                if branch_label is not None:
                    raise RuntimeError("nested parallel forks are not supported in v1")
                join_id = graph.fork_joins[current_id]
                edges = graph.outgoing[current_id]
                details.update({"join_node_id": join_id, "branch_labels": [edge.label for edge in edges]})
                executed.append(current_id)
                event_emitter.emit("NODE_COMPLETED", node, details)
                parallel_result = run_parallel(edges, join_id, path_state)
                path_state.clear()
                path_state.update(parallel_result.state)
                executed.extend(parallel_result.executed)
                providers.extend(parallel_result.providers)
                citations.extend(parallel_result.citations)
                stats.merge(parallel_result.stats)
                primary_provider = parallel_result.primary_provider or primary_provider
                pending_join_details[join_id] = parallel_result.join_details
                current_id = join_id
                continue

            next_id, branch_details = graph.next_node(node, path_state)
            details.update(branch_details)
            if node_type == "parallel.join":
                details.update(pending_join_details.pop(current_id, {}))
            executed.append(current_id)
            event_emitter.emit("NODE_COMPLETED", node, details)
            current_id = next_id

        if stop_before is not None and current_id != stop_before:
            raise RuntimeError(f"parallel branch did not reach join {stop_before}")
        return _PathResult(path_state, executed, providers, citations, stats, primary_provider)

    seed = None
    start_node_id = graph.entry_node_id
    if resume_checkpoint:
        executed_seed = resume_checkpoint.get("executed_node_ids") or []
        providers_seed = resume_checkpoint.get("providers") or []
        citations_seed = resume_checkpoint.get("citations") or []
        if not isinstance(executed_seed, list) or not all(isinstance(item, str) for item in executed_seed):
            raise RuntimeError("approval checkpoint executed_node_ids must be a string array")
        if not isinstance(providers_seed, list) or not isinstance(citations_seed, list):
            raise RuntimeError("approval checkpoint providers and citations must be arrays")
        if str(resume_checkpoint.get("node_id") or "") != resume_node_id:
            raise RuntimeError("approval resume node does not match its checkpoint")
        seed = _PathResult(
            initial_state,
            list(executed_seed),
            list(providers_seed),
            list(citations_seed),
            _ExecutionStats.from_dict(resume_checkpoint.get("stats")),
            resume_checkpoint.get("primary_provider"),
        )
        start_node_id = resume_node_id
    workflow_result = run_path(start_node_id, initial_state, seed=seed)
    executed = workflow_result.executed
    state = workflow_result.state
    if not executed or graph.nodes[executed[-1]].get("type") != "end":
        raise RuntimeError("workflow path did not terminate at an end node")
    answer = str(state.get("answer") or state.get("text") or "").strip()
    if not answer:
        raise RuntimeError("workflow produced an empty answer")
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    stats = workflow_result.stats
    return {
        "provider_mode": provider_mode,
        "output_payload": {
            "answer": answer,
            "provider": workflow_result.primary_provider or (workflow_result.providers[-1] if workflow_result.providers else "none"),
            "provider_cache_hit": stats.provider_cache_hits > 0,
            "tool_cache_hit": stats.tool_cache_hits > 0,
            "branches": state.get("branches", {}),
        },
        "citations": workflow_result.citations,
        "metrics": {
            "duration_ms": duration_ms,
            "input_chars": len(original_question),
            "output_chars": len(answer),
            "cost": 0.0,
            "executed_node_count": len(executed),
            "executed_node_ids": executed,
            "provider_call_count": stats.provider_calls,
            "provider_cache_hits": stats.provider_cache_hits,
            "tool_call_count": stats.tool_calls,
            "tool_cache_hits": stats.tool_cache_hits,
            "retrieval_citation_count": len(workflow_result.citations),
            "node_attempt_count": stats.node_attempt_count,
            "node_retry_count": stats.node_retry_count,
            "parallel_fork_count": stats.parallel_fork_count,
            "parallel_branch_count": stats.parallel_branch_count,
            "parallel_max_concurrency": stats.parallel_max_concurrency,
            "legacy_implicit_edges": graph.legacy_implicit_edges,
        },
    }


def _interruptible_sleep(delay_ms: int, should_cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + max(0, delay_ms) / 1000.0
    while True:
        _raise_if_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))
