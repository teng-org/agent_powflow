import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from runtime.workflow_runtime import WorkflowSuspended, execute_workflow


def payload(node_types=None):
    types = node_types or ["start", "llm.generate", "end"]
    return {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "workflow_checksum": "a" * 64,
        "workflow_version_no": 3,
        "workflow_dsl": {"nodes": [{"id": str(index), "type": node_type} for index, node_type in enumerate(types)]},
        "input_payload": {"text": "hello"},
    }


def payload_with_llm_config(config):
    item = payload()
    item["workflow_dsl"]["nodes"][1]["config"] = config
    return item


class WorkflowRuntimeTest(unittest.TestCase):
    def test_mock_workflow_returns_deterministic_zero_cost_result(self):
        with patch.dict(os.environ, {"WORKFLOW_PROVIDER_MODE": "mock"}):
            result = execute_workflow(payload())

        self.assertEqual("mock", result["provider_mode"])
        self.assertEqual("local-mock", result["output_payload"]["provider"])
        self.assertIn("hello", result["output_payload"]["answer"])
        self.assertEqual(0.0, result["metrics"]["cost"])

    def test_published_skill_snapshot_is_callable_in_mock_mode(self):
        item = payload(["start", "skill.call", "end"])
        item["workflow_dsl"]["nodes"][1]["config"] = {
            "skill_id": "skill-1", "skill_name": "摘要", "version": 2,
            "definition": {"instructions": "Summarize faithfully", "model": "main"},
        }
        with patch.dict(os.environ, {"WORKFLOW_PROVIDER_MODE": "mock"}):
            result = execute_workflow(item)
        self.assertIn("mock:skill:摘要", result["output_payload"]["answer"])

    def test_mcp_tool_uses_pinned_snapshot_and_returns_tool_result(self):
        item = payload(["start", "tool.mcp", "end"])
        item["workflow_dsl"]["nodes"][1]["config"] = {
            "connection_id": "mcp-1", "endpoint_url": "https://mcp.example.test/mcp",
            "tool_name": "lookup", "arguments": {"query": "${text}"}, "auth": {"type": "NONE"},
        }
        class Store:
            def claim(self, **kwargs): return SimpleNamespace(invocation_key=kwargs["invocation_key"], owner_token="owner", cached_response=None)
            def succeed(self, claim, response): self.response = response
            def fail(self, claim, error): raise AssertionError(error)
        class Executor:
            def execute(self, request, should_cancel=None):
                method = request["body"].get("method")
                if method == "initialize": return {"body": {"jsonrpc":"2.0","id":"init","result":{"protocolVersion":"2025-11-25"}}, "mcp_session_id":"session-1"}
                self.request = request
                return {"body": {"jsonrpc":"2.0","id":"call","result":{"structuredContent":{"value":"found"}}}}
        executor = Executor()
        result = execute_workflow(item, invocation_store=Store(), http_executor=executor)
        self.assertEqual("lookup", executor.request["body"]["params"]["name"])
        self.assertEqual("hello", executor.request["body"]["params"]["arguments"]["query"])
        self.assertIn("found", result["output_payload"]["answer"])
        self.assertEqual(1, result["metrics"]["tool_call_count"])

    def test_unsupported_node_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported workflow node types"):
            execute_workflow(payload(["start", "code.execute", "end"]))

    def test_non_mock_provider_mode_is_not_silently_accepted(self):
        with patch.dict(os.environ, {"WORKFLOW_PROVIDER_MODE": "production"}):
            with self.assertRaisesRegex(RuntimeError, "unsupported workflow provider mode"):
                execute_workflow(payload())

    def test_mock_failure_is_explicit_and_bounded_to_mock_config(self):
        with self.assertRaisesRegex(RuntimeError, "mock workflow failure requested"):
            execute_workflow(payload_with_llm_config({"mock_fail": True}))

    def test_mock_delay_can_be_interrupted(self):
        started = __import__("time").monotonic()

        def cancelled():
            return __import__("time").monotonic() - started > 0.05

        with self.assertRaisesRegex(RuntimeError, "TASK_CANCELLED"):
            execute_workflow(payload_with_llm_config({"mock_delay_ms": 1000}), should_cancel=cancelled)
        self.assertLess(__import__("time").monotonic() - started, 0.5)

    def test_api_strict_uses_stable_idempotency_key_and_caches_result(self):
        class Store:
            def __init__(self):
                self.claim_args = None
                self.succeeded = None

            def claim(self, **kwargs):
                self.claim_args = kwargs
                return SimpleNamespace(invocation_key=kwargs["invocation_key"], owner_token="owner", cached_response=None)

            def succeed(self, claim, response):
                self.succeeded = response

            def fail(self, claim, error):
                raise AssertionError(error)

        class Gateway:
            def __init__(self):
                self.key = ""

            def chat_completion(self, tenant_id, messages, **kwargs):
                self.key = kwargs["idempotency_key"]
                return {"text": "real answer", "endpoint": {"provider": "openai"}}

        store = Store()
        gateway = Gateway()
        with patch.dict(os.environ, {"WORKFLOW_PROVIDER_MODE": "api_strict"}):
            result = execute_workflow(payload(), invocation_store=store, gateway=gateway)

        self.assertEqual("api_strict", result["provider_mode"])
        self.assertEqual("real answer", result["output_payload"]["answer"])
        self.assertEqual(store.claim_args["invocation_key"], gateway.key)
        self.assertEqual("real answer", store.succeeded["text"])

    def test_api_strict_returns_cached_provider_result_without_calling_gateway(self):
        class Store:
            def claim(self, **kwargs):
                return SimpleNamespace(
                    invocation_key=kwargs["invocation_key"], owner_token="", cached_response={"text": "cached"}
                )

        class Gateway:
            def chat_completion(self, *args, **kwargs):
                raise AssertionError("gateway must not be called for a successful cached invocation")

        with patch.dict(os.environ, {"WORKFLOW_PROVIDER_MODE": "api_strict"}):
            result = execute_workflow(payload(), invocation_store=Store(), gateway=Gateway())
        self.assertEqual("cached", result["output_payload"]["answer"])
        self.assertTrue(result["output_payload"]["provider_cache_hit"])

    def test_graph_executes_only_the_selected_condition_branch(self):
        item = payload()
        item["input_payload"] = {"text": "hello", "plan": "premium"}
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "route", "type": "condition.if", "config": {"field": "plan", "operator": "equals", "value": "premium"}},
                {"id": "premium", "type": "llm.generate"},
                {"id": "standard", "type": "llm.generate", "config": {"mock_fail": True}},
                {"id": "premium-end", "type": "end"},
                {"id": "standard-end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "route"},
                {"id": "e2", "source_node_id": "route", "target_node_id": "premium", "condition_label": "true"},
                {"id": "e3", "source_node_id": "route", "target_node_id": "standard", "condition_label": "false"},
                {"id": "e4", "source_node_id": "premium", "target_node_id": "premium-end"},
                {"id": "e5", "source_node_id": "standard", "target_node_id": "standard-end"},
            ],
        }

        result = execute_workflow(item)

        self.assertEqual(["start", "route", "premium", "premium-end"], result["metrics"]["executed_node_ids"])
        self.assertIn("mock:premium", result["output_payload"]["answer"])

    def test_graph_passes_previous_llm_answer_to_the_next_llm(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "draft", "type": "llm.generate"},
                {"id": "polish", "type": "llm.generate"},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "draft"},
                {"id": "e2", "source_node_id": "draft", "target_node_id": "polish"},
                {"id": "e3", "source_node_id": "polish", "target_node_id": "end"},
            ],
        }

        result = execute_workflow(item)

        self.assertEqual(2, result["metrics"]["provider_call_count"])
        self.assertIn("[mock:draft] hello", result["output_payload"]["answer"])
        self.assertTrue(result["output_payload"]["answer"].startswith("[mock:polish]"))

    def test_human_approval_persists_checkpoint_and_resumes_selected_branch(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "draft", "type": "llm.generate"},
                {"id": "approval", "type": "human.approval", "config": {
                    "title": "Publish answer", "instructions": "Review the generated answer.",
                    "assignee_roles": ["TENANT_ADMIN"], "timeout_seconds": 3600, "timeout_action": "reject",
                }},
                {"id": "approved", "type": "llm.generate"},
                {"id": "rejected", "type": "llm.generate", "config": {"mock_fail": True}},
                {"id": "approved-end", "type": "end"},
                {"id": "rejected-end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "draft"},
                {"id": "e2", "source_node_id": "draft", "target_node_id": "approval"},
                {"id": "e3", "source_node_id": "approval", "target_node_id": "approved", "condition_label": "approve"},
                {"id": "e4", "source_node_id": "approval", "target_node_id": "rejected", "condition_label": "reject"},
                {"id": "e5", "source_node_id": "approved", "target_node_id": "approved-end"},
                {"id": "e6", "source_node_id": "rejected", "target_node_id": "rejected-end"},
            ],
        }
        events = []
        with self.assertRaises(WorkflowSuspended) as suspended:
            execute_workflow(item, on_node_event=events.append)

        pause = suspended.exception
        self.assertEqual("approval", pause.node_id)
        self.assertEqual(["TENANT_ADMIN"], pause.assignee_roles)
        self.assertEqual(["start", "draft"], pause.checkpoint["executed_node_ids"])
        self.assertEqual("[mock:draft] hello", pause.checkpoint["state"]["answer"])
        self.assertEqual("NODE_WAITING_APPROVAL", events[-1]["event_type"])

        item["approval_resume"] = {
            "approval_id": "approval-1", "node_id": "approval", "decision": "approve",
            "decided_by": "admin@example.test", "decision_reason": "looks good",
            "decided_at": "2026-08-03T12:00:00Z", "timed_out": False, "checkpoint": pause.checkpoint,
        }
        resumed_events = []
        result = execute_workflow(item, on_node_event=resumed_events.append)

        self.assertEqual(
            ["start", "draft", "approval", "approved", "approved-end"], result["metrics"]["executed_node_ids"]
        )
        self.assertTrue(result["output_payload"]["answer"].startswith("[mock:approved]"))
        self.assertTrue(all(event["progress"] >= pause.progress for event in resumed_events))
        self.assertEqual(sorted(event["progress"] for event in resumed_events), [event["progress"] for event in resumed_events])
        completed = next(
            event for event in resumed_events
            if event["node_id"] == "approval" and event["event_type"] == "NODE_COMPLETED"
        )
        self.assertEqual("approve", completed["details"]["approval_decision"])
        self.assertEqual("approval-1", completed["details"]["approval_id"])

    def test_human_approval_requires_explicit_branches(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "approval", "type": "human.approval", "config": {"title": "Review", "assignee_roles": []}},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "approval"},
                {"id": "e2", "source_node_id": "approval", "target_node_id": "end", "condition_label": "approve"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "requires exactly approve and reject branches"):
            execute_workflow(item)

    def test_human_approval_inside_parallel_branch_is_rejected(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"}, {"id": "fork", "type": "parallel.fork"},
                {"id": "approval", "type": "human.approval", "config": {"title": "Review", "assignee_roles": ["TENANT_ADMIN"]}},
                {"id": "approval-reject", "type": "llm.generate"}, {"id": "other", "type": "llm.generate"},
                {"id": "join", "type": "parallel.join", "config": {"answer_branch": "review"}},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "fork"},
                {"id": "e2", "source_node_id": "fork", "target_node_id": "approval", "condition_label": "review"},
                {"id": "e3", "source_node_id": "fork", "target_node_id": "other", "condition_label": "other"},
                {"id": "e4", "source_node_id": "approval", "target_node_id": "join", "condition_label": "approve"},
                {"id": "e5", "source_node_id": "approval", "target_node_id": "approval-reject", "condition_label": "reject"},
                {"id": "e6", "source_node_id": "approval-reject", "target_node_id": "join"},
                {"id": "e7", "source_node_id": "other", "target_node_id": "join"},
                {"id": "e8", "source_node_id": "join", "target_node_id": "end"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "human approval nodes inside parallel branches"):
            execute_workflow(item)

    def test_parallel_fork_runs_branches_concurrently_and_joins_deterministically(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "fork", "type": "parallel.fork"},
                {"id": "draft", "type": "llm.generate"},
                {"id": "review", "type": "llm.generate"},
                {"id": "join", "type": "parallel.join", "config": {"answer_branch": "draft"}},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "fork"},
                {"id": "e2", "source_node_id": "fork", "target_node_id": "draft", "condition_label": "draft"},
                {"id": "e3", "source_node_id": "fork", "target_node_id": "review", "condition_label": "review"},
                {"id": "e4", "source_node_id": "draft", "target_node_id": "join"},
                {"id": "e5", "source_node_id": "review", "target_node_id": "join"},
                {"id": "e6", "source_node_id": "join", "target_node_id": "end"},
            ],
        }
        original_execute = __import__("runtime.workflow_runtime", fromlist=["node_adapter_registry"]).node_adapter_registry.execute
        barrier = threading.Barrier(2)
        active = 0
        max_active = 0
        active_lock = threading.Lock()

        def concurrent_execute(node, context):
            nonlocal active, max_active
            if node.get("id") not in {"draft", "review"}:
                return original_execute(node, context)
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                barrier.wait(timeout=1)
                return original_execute(node, context)
            finally:
                with active_lock:
                    active -= 1

        events = []
        with patch("runtime.workflow_runtime.node_adapter_registry.execute", side_effect=concurrent_execute):
            result = execute_workflow(item, on_node_event=events.append)

        self.assertEqual(2, max_active)
        self.assertEqual("[mock:draft] hello", result["output_payload"]["answer"])
        self.assertEqual(["start", "fork", "draft", "review", "join", "end"], result["metrics"]["executed_node_ids"])
        self.assertEqual(1, result["metrics"]["parallel_fork_count"])
        self.assertEqual(2, result["metrics"]["parallel_branch_count"])
        self.assertEqual(2, result["metrics"]["parallel_max_concurrency"])
        self.assertEqual("[mock:review] hello", result["output_payload"]["branches"]["review"]["answer"])
        self.assertEqual(sorted(event["progress"] for event in events), [event["progress"] for event in events])
        branch_events = [event for event in events if event["node_id"] in {"draft", "review"}]
        self.assertTrue(all(event["details"].get("parallel_branch") in {"draft", "review"} for event in branch_events))

    def test_parallel_branch_failure_is_aggregated_with_its_label(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"}, {"id": "fork", "type": "parallel.fork"},
                {"id": "bad", "type": "llm.generate", "config": {"mock_fail": True}},
                {"id": "good", "type": "llm.generate", "config": {"mock_delay_ms": 100}},
                {"id": "join", "type": "parallel.join", "config": {"answer_branch": "good"}},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "fork"},
                {"id": "e2", "source_node_id": "fork", "target_node_id": "bad", "condition_label": "bad"},
                {"id": "e3", "source_node_id": "fork", "target_node_id": "good", "condition_label": "good"},
                {"id": "e4", "source_node_id": "bad", "target_node_id": "join"},
                {"id": "e5", "source_node_id": "good", "target_node_id": "join"},
                {"id": "e6", "source_node_id": "join", "target_node_id": "end"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "parallel branch bad failed: mock workflow failure requested"):
            execute_workflow(item)

    def test_parallel_graph_rejects_missing_join_and_implicit_answer_selection(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"}, {"id": "fork", "type": "parallel.fork"},
                {"id": "a", "type": "llm.generate"}, {"id": "b", "type": "llm.generate"},
                {"id": "end-a", "type": "end"}, {"id": "end-b", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "fork"},
                {"id": "e2", "source_node_id": "fork", "target_node_id": "a", "condition_label": "a"},
                {"id": "e3", "source_node_id": "fork", "target_node_id": "b", "condition_label": "b"},
                {"id": "e4", "source_node_id": "a", "target_node_id": "end-a"},
                {"id": "e5", "source_node_id": "b", "target_node_id": "end-b"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "reached an end node before a join"):
            execute_workflow(item)

    def test_node_events_are_ordered_and_do_not_include_full_state(self):
        events = []
        result = execute_workflow(payload(), on_node_event=events.append)

        self.assertEqual(6, len(events))
        self.assertEqual(["NODE_STARTED", "NODE_COMPLETED"] * 3, [event["event_type"] for event in events])
        self.assertEqual(sorted(event["progress"] for event in events), [event["progress"] for event in events])
        self.assertTrue(all("state" not in event for event in events))
        self.assertEqual(3, result["metrics"]["executed_node_count"])

    def test_runtime_rejects_unreachable_nodes_even_if_validation_was_bypassed(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"}, {"id": "llm", "type": "llm.generate"},
                {"id": "end", "type": "end"}, {"id": "orphan", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "llm"},
                {"id": "e2", "source_node_id": "llm", "target_node_id": "end"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "unreachable nodes: orphan"):
            execute_workflow(item)

    def test_retrieval_node_enforces_access_and_propagates_citations(self):
        item = payload()
        item["workflow_dsl"] = {
            "entry_node_id": "start",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "search", "type": "retrieval.search", "config": {"kb_id": "shared-kb", "top_k": 3}},
                {"id": "answer", "type": "llm.generate"},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source_node_id": "start", "target_node_id": "search"},
                {"id": "e2", "source_node_id": "search", "target_node_id": "answer"},
                {"id": "e3", "source_node_id": "answer", "target_node_id": "end"},
            ],
        }
        calls = []

        def resolve(tenant_id, kb_id):
            calls.append((tenant_id, kb_id))
            return "owner-tenant"

        def search(req):
            self.assertEqual("owner-tenant", req.tenant_id)
            self.assertEqual("shared-kb", req.kb_id)
            return [{"doc_id": "d1", "chunk_id": "c1", "chunk_index": 2, "text": "evidence", "source": "guide.md", "score": 0.91}]

        result = execute_workflow(item, retrieval_search=search, kb_access_resolver=resolve)

        self.assertEqual([("tenant-a", "shared-kb")], calls)
        self.assertEqual(1, result["metrics"]["retrieval_citation_count"])
        self.assertEqual("c1", result["citations"][0]["chunk_id"])

    def test_retrieval_node_fails_closed_when_kb_is_not_accessible(self):
        item = payload(["start", "retrieval.search", "llm.generate", "end"])
        item["workflow_dsl"]["nodes"][1]["config"] = {"kb_id": "private-kb"}
        with self.assertRaisesRegex(RuntimeError, "not accessible"):
            execute_workflow(item, retrieval_search=lambda req: [], kb_access_resolver=lambda tenant, kb: None)

    def test_http_tool_is_idempotent_and_passes_stable_key(self):
        item = payload(["start", "tool.http", "llm.generate", "end"])
        item["workflow_dsl"]["nodes"][1]["config"] = {
            "url": "https://api.example.test/v1/items",
            "method": "POST",
            "body": {"message": "${text}"},
        }

        class Store:
            def claim(self, **kwargs):
                self.args = kwargs
                return SimpleNamespace(invocation_key=kwargs["invocation_key"], owner_token="owner", cached_response=None)
            def succeed(self, claim, response): self.response = response
            def fail(self, claim, error): raise AssertionError(error)

        class Executor:
            def execute(self, request, should_cancel):
                self.request = request
                return {"status_code": 200, "content_type": "application/json", "body": {"ok": True}, "url": request["url"]}

        store, executor = Store(), Executor()
        result = execute_workflow(item, invocation_store=store, http_executor=executor)

        self.assertEqual(store.args["invocation_key"], executor.request["headers"]["Idempotency-Key"])
        self.assertEqual("hello", executor.request["body"]["message"])
        self.assertEqual(1, result["metrics"]["tool_call_count"])
        self.assertIn('{"ok": true}', result["output_payload"]["answer"])

    def test_http_tool_injects_tenant_secret_without_persisting_plaintext(self):
        item = payload(["start", "tool.http", "llm.generate", "end"])
        item["workflow_dsl"]["nodes"][1]["config"] = {
            "url": "https://api.example.test/v1/items",
            "auth": {"secret_ref": "billing-api", "header": "Authorization", "prefix": "Bearer "},
        }

        class Store:
            def claim(self, **kwargs):
                self.args = kwargs
                return SimpleNamespace(invocation_key=kwargs["invocation_key"], owner_token="owner", cached_response=None)
            def succeed(self, claim, response): pass
            def fail(self, claim, error): raise AssertionError(error)

        class Executor:
            def execute(self, request, should_cancel):
                self.request = request
                return {"status_code": 200, "content_type": "application/json", "body": {"ok": True}, "url": request["url"]}

        def resolve(tenant_id, reference):
            self.assertEqual(("tenant-a", "billing-api"), (tenant_id, reference))
            return SimpleNamespace(secret_id="secret-1", value="super-secret", fingerprint="f00baa", version=4)

        store, executor = Store(), Executor()
        execute_workflow(item, invocation_store=store, http_executor=executor, secret_resolver=resolve)

        self.assertEqual("Bearer super-secret", executor.request["headers"]["Authorization"])
        self.assertEqual(["Authorization"], executor.request["trusted_header_names"])
        self.assertNotIn("super-secret", str(store.args))

    def test_failed_node_emits_minimal_failure_event(self):
        events = []
        with self.assertRaisesRegex(RuntimeError, "mock workflow failure requested"):
            execute_workflow(payload_with_llm_config({"mock_fail": True}), on_node_event=events.append)
        failed = events[-1]
        self.assertEqual("NODE_FAILED", failed["event_type"])
        self.assertEqual("llm.generate", failed["node_type"])
        self.assertNotIn("state", failed)

    def test_explicit_node_retry_emits_attempt_audit_and_recovers(self):
        item = payload_with_llm_config({
            "retry": {
                "max_attempts": 3,
                "initial_delay_ms": 0,
                "max_delay_ms": 0,
                "retry_on": "any",
            }
        })
        events = []
        original_execute = __import__("runtime.workflow_runtime", fromlist=["node_adapter_registry"]).node_adapter_registry.execute
        calls = 0

        def flaky_execute(node, context):
            nonlocal calls
            if node.get("type") == "llm.generate":
                calls += 1
                if calls < 3:
                    raise RuntimeError("temporary provider failure")
            return original_execute(node, context)

        with patch("runtime.workflow_runtime.node_adapter_registry.execute", side_effect=flaky_execute):
            result = execute_workflow(item, on_node_event=events.append)

        attempt_events = [event for event in events if event["event_type"].startswith("NODE_ATTEMPT_")]
        self.assertEqual(
            ["NODE_ATTEMPT_STARTED", "NODE_ATTEMPT_FAILED", "NODE_ATTEMPT_STARTED", "NODE_ATTEMPT_FAILED", "NODE_ATTEMPT_STARTED", "NODE_ATTEMPT_SUCCEEDED"],
            [event["event_type"] for event in attempt_events],
        )
        self.assertEqual([1, 1, 2, 2, 3, 3], [event["details"]["attempt_no"] for event in attempt_events])
        self.assertEqual(5, result["metrics"]["node_attempt_count"])
        self.assertEqual(2, result["metrics"]["node_retry_count"])
        completed = next(event for event in events if event["event_type"] == "NODE_COMPLETED" and event["node_type"] == "llm.generate")
        self.assertEqual(3, completed["details"]["attempt_count"])

    def test_retry_policy_never_retries_cancellation(self):
        item = payload_with_llm_config({
            "mock_delay_ms": 100,
            "retry": {"max_attempts": 5, "initial_delay_ms": 0, "retry_on": "any"},
        })
        events = []
        checks = 0
        def cancel_during_llm():
            nonlocal checks
            checks += 1
            return checks >= 4
        with self.assertRaisesRegex(RuntimeError, "TASK_CANCELLED"):
            execute_workflow(item, should_cancel=cancel_during_llm, on_node_event=events.append)
        self.assertEqual(1, sum(event["event_type"] == "NODE_ATTEMPT_STARTED" for event in events))
        failed = [event for event in events if event["event_type"] == "NODE_ATTEMPT_FAILED"]
        self.assertEqual(1, len(failed))
        self.assertFalse(failed[0]["details"]["will_retry"])


if __name__ == "__main__":
    unittest.main()
