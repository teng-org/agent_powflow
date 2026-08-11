from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


CONTROL = "http://127.0.0.1:8099"
CHAT = "http://127.0.0.1:8092"
SOURCE_DIR = Path(r"E:\download\BaiduNetdisk\AI大模型\电子书")
ARTIFACT_DIR = Path("artifacts/e2e-agent-20260811")
STATE_PATH = ARTIFACT_DIR / "state.json"
RESULT_PATH = ARTIFACT_DIR / "results.json"
KB_NAME = "E2E-20260811-电子书知识库"
AGENT_NAME = "E2E-20260811-大模型学习助手"
DEPLOYMENT_NAME = "E2E-20260811-学习助手 API"
TERMINAL_TASK_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTER"}
READY_DOCUMENT_STATES = {"READY", "INDEXED", "SUCCEEDED"}

ACCEPTED_BOOKS = [
    "中国人工智能系列白皮书——大模型技术（2023+版）+(陶建华,+吴飞,+黄民烈,+文继荣,+王海峰,+刘知远,+刘静,+杨小康,+聂帅)+(Z-Library).pdf",
    "大模型应用开发极简入门：基于+GPT-4+和+ChatGPT.pdf",
    "大模型落地应用案例集.pdf",
    "大规模语言模型：从理论到实践.pdf",
    "自然语言处理白皮书&发展现状.pdf",
]
OVERSIZE_BOOK = "动手做AI Agent .pdf"

SINGLE_TURN_QUESTIONS = [
    "请基于知识库概括大模型的核心技术体系，并列出三项关键能力。",
    "你上一轮提到的三项能力中，哪一项最依赖数据质量？说明原因。",
    "结合知识库，解释预训练、指令微调和人类反馈对齐之间的关系。",
    "延续前面的解释，给一个企业落地大模型的四阶段实施路线。",
    "知识库中的典型应用案例主要分布在哪些行业？请归纳而非编造具体数字。",
    "从这些案例中选择一种适合中小企业的场景，说明选择依据。",
    "如果要为该场景建立知识库，应如何准备文档、切片和检索策略？",
    "比较向量检索与混合检索在这个场景中的优缺点。",
    "回顾我们前面的讨论，列出你已经提出的实施风险，并按优先级排序。",
    "针对最高优先级风险给出可执行的缓解措施和验收指标。",
    "请说明大模型应用评测应同时覆盖哪些质量、性能与安全指标。",
    "最后把本次对话压缩成一份不超过八条的项目启动清单。",
]

CONCURRENT_DIALOGUES = [
    [
        "基于知识库解释 Transformer 为什么适合大模型。",
        "继续比较它与传统循环神经网络的差异。",
        "给出企业选型时需要关注的三个工程指标。",
        "总结以上讨论并指出知识库依据不足的部分。",
    ],
    [
        "知识库中的大模型落地案例有哪些共同成功因素？",
        "从组织和数据两个角度进一步分析。",
        "如果预算有限，应该先验证哪个最小场景？",
        "形成一份四步试点计划。",
    ],
    [
        "请解释 RAG 系统的基本组成。",
        "上一轮的检索阶段可能出现哪些失败？",
        "如何用指标发现这些失败？",
        "给出上线前检查清单。",
    ],
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return round(ordered[index], 2)


class AcceptanceRunner:
    def __init__(self, password: str) -> None:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        self.password = password
        self.token = ""
        self.api_key = ""
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.state = self._load_json(STATE_PATH, {})
        self.results = self._load_json(RESULT_PATH, {"started_at": utc_now(), "events": [], "issues": []})

    @staticmethod
    def _load_json(path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def save(self) -> None:
        with self.lock:
            self._write_json(STATE_PATH, self.state)
            self._write_json(RESULT_PATH, self.results)

    def event(self, phase: str, action: str, elapsed_ms: float, ok: bool, **details: Any) -> None:
        row = {
            "ts": utc_now(), "phase": phase, "action": action,
            "elapsed_ms": round(elapsed_ms, 2), "ok": ok, **details,
        }
        with self.lock:
            self.results["events"].append(row)
        self.save()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def issue(self, phase: str, code: str, message: str, **details: Any) -> None:
        row = {"ts": utc_now(), "phase": phase, "code": code, "message": message, **details}
        with self.lock:
            self.results["issues"].append(row)
        self.save()
        print("ISSUE " + json.dumps(row, ensure_ascii=False), flush=True)

    def headers(self, json_body: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "X-Source-Service": "e2e-agent-acceptance"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, phase: str, action: str, method: str, url: str, *, expected=(200,), **kwargs: Any) -> Any:
        started = time.perf_counter()
        response = self.session.request(method, url, timeout=kwargs.pop("timeout", 120), **kwargs)
        elapsed = (time.perf_counter() - started) * 1000
        ok = response.status_code in expected
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text[:500]}
        self.event(phase, action, elapsed, ok, http_status=response.status_code)
        if not ok:
            raise RuntimeError(f"{action} failed: HTTP {response.status_code}: {data}")
        return data

    def login(self) -> None:
        data = self.request(
            "baseline", "login", "POST", f"{CONTROL}/api/v1/auth/login",
            headers={"Content-Type": "application/json", "X-Source-Service": "e2e-agent-acceptance"},
            json={"tenantCode": "platform", "email": "admin", "password": self.password},
        )
        self.token = str(data.get("accessToken") or "")
        if not self.token:
            raise RuntimeError("login returned no access token")
        self.state.update({"tenant_id": data.get("tenantId"), "actor_id": data.get("actorId")})
        self.save()

    def ensure_knowledge_base(self) -> str:
        items = self.request(
            "knowledge", "list knowledge bases", "GET", f"{CONTROL}/api/v1/knowledge-bases",
            headers=self.headers(False),
        ).get("items", [])
        item = next((x for x in items if x.get("name") == KB_NAME), None)
        if not item:
            item = self.request(
                "knowledge", "create knowledge base", "POST", f"{CONTROL}/api/v1/knowledge-bases",
                headers=self.headers(), json={
                    "name": KB_NAME,
                    "description": "Powflow 完整 Agent 端到端验收：真实电子书、多轮对话、并发与 API 外部调用。",
                    "tags": ["E2E", "大模型", "电子书"], "visibility": "PRIVATE",
                },
            ).get("item")
        kb_id = str((item or {}).get("kb_id") or "")
        if not kb_id:
            raise RuntimeError("knowledge base id missing")
        self.state["kb_id"] = kb_id
        self.save()
        return kb_id

    def upload_files(self, kb_id: str) -> None:
        documents = self.request(
            "knowledge", "inspect documents before recovery", "GET",
            f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents?limit=100",
            headers=self.headers(False),
        ).get("items", [])
        documents_by_id = {str(item.get("doc_id") or ""): item for item in documents}
        source_documents = self.state.setdefault("source_documents", {})
        for item in documents:
            filename = str(item.get("filename") or "")
            doc_id = str(item.get("doc_id") or "")
            if filename in ACCEPTED_BOOKS and doc_id:
                source_documents[filename] = doc_id
        for task in self.state.get("document_tasks", {}).values():
            filename = str(task.get("filename") or "")
            doc_id = str(task.get("doc_id") or "")
            if filename and doc_id:
                source_documents.setdefault(filename, doc_id)
        tracked_ids = {str(doc_id) for doc_id in source_documents.values() if doc_id}
        ready_ids = {
            doc_id for doc_id in tracked_ids
            if str(documents_by_id.get(doc_id, {}).get("status") or "").upper() in READY_DOCUMENT_STATES
        }
        if self.state.get("upload_completed") and len(ready_ids) >= len(ACCEPTED_BOOKS):
            return
        self.state["upload_completed"] = False
        self.save()
        oversize = SOURCE_DIR / OVERSIZE_BOOK
        if oversize.exists() and not self.state.get("oversize_checked"):
            with oversize.open("rb") as stream:
                data = self.request(
                    "knowledge", "oversize upload boundary", "POST",
                    f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents/upload",
                    headers=self.headers(False), files=[("file", (oversize.name, stream, "application/pdf"))],
                    data={"process_mode": "UPLOAD_CLEAN_CHUNK_EMBED", "chunk_method": "sentence", "chunk_size": "700", "chunk_overlap": "100"},
                    timeout=300, expected=(200, 413),
                )
            self.state["oversize_checked"] = True
            self.state["upload_limits"] = {
                "max_file_size_bytes": data.get("max_file_size_bytes"),
                "max_batch_size_bytes": data.get("max_batch_size_bytes"),
                "max_files_per_request": data.get("max_files_per_request"),
            }
            if data.get("accepted_count"):
                self.issue("knowledge", "OVERSIZE_ACCEPTED", "预期的大文件边界样本被接受，实际上传限制高于界面提示")
            else:
                self.issue("knowledge", "UPLOAD_LIMIT", "大文件被上传限制拒绝；HTTP 层 413 时响应体为空", skipped=data.get("skipped", []))
            self.save()

        existing = self.request(
            "knowledge", "list documents before upload", "GET",
            f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents?limit=100",
            headers=self.headers(False),
        )
        existing_names = {str(x.get("filename") or "") for x in existing.get("items", [])}
        known_names = existing_names | set(source_documents)
        paths = [SOURCE_DIR / name for name in ACCEPTED_BOOKS if name not in known_names]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise RuntimeError(f"source files missing: {missing}")
        if paths:
            streams = [path.open("rb") for path in paths]
            try:
                files = [("file", (path.name, stream, "application/pdf")) for path, stream in zip(paths, streams)]
                data = self.request(
                    "knowledge", "upload representative PDFs", "POST",
                    f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents/upload",
                    headers=self.headers(False), files=files,
                    data={"process_mode": "UPLOAD_CLEAN_CHUNK_EMBED", "chunk_method": "sentence", "chunk_size": "700", "chunk_overlap": "100"},
                    timeout=600,
                )
            finally:
                for stream in streams:
                    stream.close()
            if data.get("skipped_count"):
                self.issue("knowledge", "UPLOAD_SKIPPED", "代表性 PDF 上传存在 skipped 项", skipped=data.get("skipped", []))
            task_rows = data.get("accepted", [])
        else:
            task_rows = []

        active_tasks: dict[str, dict[str, Any]] = {}
        historical_tasks = self.state.setdefault("document_tasks", {})
        for row in task_rows:
            task = {
                "filename": row.get("filename"), "doc_id": row.get("doc_id"), "accepted_at": utc_now(),
            }
            historical_tasks[row["task_id"]] = task
            active_tasks[row["task_id"]] = task
            if row.get("filename") and row.get("doc_id"):
                source_documents[str(row["filename"])] = str(row["doc_id"])

        if active_tasks:
            self.state["active_document_tasks"] = active_tasks
            self.save()
            self.wait_for_document_tasks(active_tasks)

        failed_doc_ids: dict[str, str] = {}
        for task in historical_tasks.values():
            if str(task.get("last_state") or "").upper() != "FAILED":
                continue
            doc_id = str(task.get("doc_id") or "")
            filename = str(task.get("filename") or "")
            current_status = str(documents_by_id.get(doc_id, {}).get("status") or "").upper()
            if doc_id and current_status == "FAILED":
                failed_doc_ids.setdefault(doc_id, filename)
        for doc_id, filename in failed_doc_ids.items():
            data = self.request(
                "knowledge", f"reindex failed document {doc_id}", "POST",
                f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/reindex",
                headers=self.headers(), json={
                    "chunk_method": "sentence", "chunk_size": 700, "chunk_overlap": 100,
                },
            )
            task_id = str(data.get("task_id") or "")
            if not task_id:
                raise RuntimeError(f"reindex did not return task id for document {doc_id}")
            task = {"filename": filename, "doc_id": doc_id, "accepted_at": utc_now(), "recovery": True}
            historical_tasks[task_id] = task
            active_tasks[task_id] = task
            self.state["active_document_tasks"] = active_tasks
            self.save()
            self.wait_for_document_tasks({task_id: task})

        all_succeeded = all(
            task.get("last_state") == "SUCCEEDED" for task in active_tasks.values()
        )
        verified = self.request(
            "knowledge", "verify recovered documents", "GET",
            f"{CONTROL}/api/v1/knowledge-bases/{kb_id}/documents?limit=100",
            headers=self.headers(False),
        ).get("items", [])
        verified_by_id = {str(item.get("doc_id") or ""): item for item in verified}
        tracked_ids = {str(doc_id) for doc_id in source_documents.values() if doc_id}
        ready_ids = {
            doc_id for doc_id in tracked_ids
            if str(verified_by_id.get(doc_id, {}).get("status") or "").upper() in READY_DOCUMENT_STATES
        }
        all_succeeded = all_succeeded and len(ready_ids) >= len(ACCEPTED_BOOKS)
        self.state["knowledge_ready_doc_ids"] = sorted(ready_ids)
        self.state["upload_completed"] = all_succeeded
        self.save()
        if not all_succeeded:
            raise RuntimeError(
                f"knowledge recovery incomplete: ready={len(ready_ids)}/{len(ACCEPTED_BOOKS)}, "
                f"active_tasks={len(active_tasks)}"
            )

    def wait_for_document_tasks(self, tasks: dict[str, dict[str, Any]] | None = None, timeout_seconds: int = 3600) -> None:
        tasks = tasks if tasks is not None else self.state.get("active_document_tasks", {})
        deadline = time.monotonic() + timeout_seconds
        pending = set(tasks)
        while pending and time.monotonic() < deadline:
            for task_id in list(pending):
                started = time.perf_counter()
                data = self.session.get(
                    f"{CONTROL}/api/v1/tasks/{task_id}", headers=self.headers(False), timeout=30,
                )
                elapsed = (time.perf_counter() - started) * 1000
                payload = data.json()
                state = str(payload.get("state") or "UNKNOWN").upper()
                task = tasks[task_id]
                previous = task.get("last_state")
                task["last_state"] = state
                task["progress_pct"] = (payload.get("record") or {}).get("progress_pct")
                if previous != state:
                    self.event("knowledge", f"task {task_id} -> {state}", elapsed, data.ok, filename=task.get("filename"))
                if state in TERMINAL_TASK_STATES:
                    task["finished_at"] = utc_now()
                    task["error_message"] = (payload.get("record") or {}).get("error_message")
                    pending.remove(task_id)
                    if state != "SUCCEEDED":
                        self.issue("knowledge", "DOCUMENT_TASK_FAILED", f"文档处理终态为 {state}", task_id=task_id, filename=task.get("filename"), error=task.get("error_message"))
                self.save()
            if pending:
                time.sleep(3)
        if pending:
            self.issue("knowledge", "DOCUMENT_TASK_TIMEOUT", "文档处理等待超时", task_ids=sorted(pending))
            raise TimeoutError(f"document tasks timed out: {sorted(pending)}")

    def ensure_agent(self, kb_id: str) -> tuple[str, int, dict[str, Any]]:
        model_data = self.request(
            "agent", "list chat models", "GET", f"{CHAT}/api/v1/chat/models", headers=self.headers(False),
        )
        models = model_data.get("items", [])
        model = next((x for x in models if x.get("is_default")), models[0] if models else None)
        if not model:
            raise RuntimeError("no chat model available")
        model_config = {
            "role": "大模型学习与企业落地顾问",
            "response_style": "优先引用知识库事实；不确定时明确说明；回答结构清晰、简洁。",
            "model_endpoint_id": model.get("endpoint_id"), "temperature": 0.2, "top_p": 0.9, "max_tokens": 1200,
            "retrieval": {"enabled": True, "top_k": 6, "rank_method": "vector", "score_threshold": 0},
            "reply_examples": [],
        }
        agents = self.request(
            "agent", "list agents", "GET", f"{CONTROL}/api/v1/agents", headers=self.headers(False),
        ).get("items", [])
        item = next((x for x in agents if x.get("name") == AGENT_NAME), None)
        payload = {
            "name": AGENT_NAME,
            "description": "基于真实大模型电子书知识库的学习与企业落地助手。",
            "instructions": "你是大模型学习与企业落地顾问。必须优先依据挂载知识库回答，区分知识库事实与推断；对没有证据的具体数字、机构或结论不要编造；对追问保持上下文一致。",
            "model_config": model_config, "knowledge_base_ids": [kb_id], "workflow_id": None,
            "visibility": "PRIVATE", "expected_revision": None,
        }
        if not item:
            item = self.request(
                "agent", "create agent", "POST", f"{CONTROL}/api/v1/agents",
                headers=self.headers(), json=payload,
            ).get("item")
        else:
            payload["expected_revision"] = item.get("revision")
            item = self.request(
                "agent", "update agent", "PUT", f"{CONTROL}/api/v1/agents/{item['agent_id']}",
                headers=self.headers(), json=payload,
            ).get("item")
        agent_id = str((item or {}).get("agent_id") or "")
        if not agent_id:
            raise RuntimeError("agent id missing")
        published = self.request(
            "agent", "publish agent", "POST", f"{CONTROL}/api/v1/agents/{agent_id}/publish",
            headers=self.headers(), json={},
        ).get("item", {})
        version_no = int(published.get("version_no") or 0)
        self.state.update({"agent_id": agent_id, "agent_version_no": version_no, "model_endpoint_id": model.get("endpoint_id")})
        self.save()
        return agent_id, version_no, model_config

    def create_chat_session(self, title: str, kb_id: str) -> str:
        data = self.request(
            "chat", "create chat session", "POST", f"{CHAT}/api/v1/chat/sessions",
            headers=self.headers(), json={"title": title, "kb_id": kb_id},
        )
        return str(data.get("item", {}).get("session_id") or "")

    def stream_turn(self, session_id: str, message: str, kb_id: str, agent_id: str, model_config: dict[str, Any], phase: str, turn: int) -> dict[str, Any]:
        payload = {
            "message": message, "use_kb": True, "use_agent": False, "kb_id": kb_id,
            "top_k": 6, "rank_method": "vector", "pipeline_id": "default-chat-v1",
            "metadata": {"source": "e2e-agent-acceptance", "agent_id": agent_id},
            "agent_config": {"agent_id": agent_id, "instructions": "优先依据知识库回答，保留上下文并明确不确定性。", **model_config},
        }
        started = time.perf_counter()
        first_token_ms = None
        output: list[str] = []
        event_names: list[str] = []
        response = requests.post(
            f"{CHAT}/api/v1/chat/sessions/{session_id}/messages/stream",
            headers=self.headers(), json=payload, stream=True, timeout=180,
        )
        response.raise_for_status()
        for raw in response.iter_lines(decode_unicode=True):
            line = (raw or "").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            event_name = str(event.get("event") or "message")
            event_names.append(event_name)
            if event_name == "token":
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                output.append(str(event.get("delta") or ""))
            elif event_name == "error":
                raise RuntimeError(str(event.get("message") or "chat stream failed"))
        total_ms = (time.perf_counter() - started) * 1000
        row = {
            "session_id": session_id, "turn": turn, "question": message,
            "first_token_ms": round(first_token_ms, 2) if first_token_ms is not None else None,
            "total_ms": round(total_ms, 2), "output_chars": len("".join(output)),
            "event_names": sorted(set(event_names)), "ok": bool(output),
        }
        self.event(phase, f"chat turn {turn}", total_ms, row["ok"], first_token_ms=row["first_token_ms"], output_chars=row["output_chars"])
        if not output:
            self.issue(phase, "EMPTY_CHAT_OUTPUT", "对话未返回 token", session_id=session_id, turn=turn, events=row["event_names"])
        return row

    def run_single_dialogue(self, kb_id: str, agent_id: str, model_config: dict[str, Any]) -> None:
        if self.state.get("single_dialogue_completed"):
            return
        session_id = self.create_chat_session(f"{AGENT_NAME} · 12轮连续对话", kb_id)
        rows = []
        for index, question in enumerate(SINGLE_TURN_QUESTIONS, 1):
            try:
                rows.append(self.stream_turn(session_id, question, kb_id, agent_id, model_config, "chat-single", index))
            except Exception as exc:
                self.issue("chat-single", "TURN_FAILED", str(exc), session_id=session_id, turn=index)
                rows.append({"session_id": session_id, "turn": index, "question": question, "ok": False, "error": str(exc)})
        self.results["single_dialogue"] = rows
        self.state["single_dialogue_completed"] = True
        self.save()

    def run_concurrent_dialogues(self, kb_id: str, agent_id: str, model_config: dict[str, Any]) -> None:
        if self.state.get("concurrent_dialogues_completed"):
            return
        sessions = [self.create_chat_session(f"{AGENT_NAME} · 并发会话 {i + 1}", kb_id) for i in range(len(CONCURRENT_DIALOGUES))]
        all_rows: list[dict[str, Any]] = []
        wall_started = time.perf_counter()
        for turn_index in range(len(CONCURRENT_DIALOGUES[0])):
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(sessions)) as pool:
                futures = [
                    pool.submit(self.stream_turn, sessions[i], CONCURRENT_DIALOGUES[i][turn_index], kb_id, agent_id, model_config, "chat-concurrent", turn_index + 1)
                    for i in range(len(sessions))
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        all_rows.append(future.result())
                    except Exception as exc:
                        self.issue("chat-concurrent", "TURN_FAILED", str(exc), turn=turn_index + 1)
                        all_rows.append({"turn": turn_index + 1, "ok": False, "error": str(exc)})
        wall_ms = (time.perf_counter() - wall_started) * 1000
        latencies = [float(row["total_ms"]) for row in all_rows if row.get("ok") and row.get("total_ms") is not None]
        self.results["concurrent_dialogues"] = {
            "session_count": len(sessions), "turns_per_session": len(CONCURRENT_DIALOGUES[0]),
            "wall_ms": round(wall_ms, 2), "success_count": sum(1 for x in all_rows if x.get("ok")),
            "failure_count": sum(1 for x in all_rows if not x.get("ok")),
            "p50_ms": percentile(latencies, 0.5), "p95_ms": percentile(latencies, 0.95), "rows": all_rows,
        }
        self.state["concurrent_dialogues_completed"] = True
        self.save()

    def ensure_deployment_and_key(self, agent_id: str, version_no: int) -> tuple[dict[str, Any], str]:
        deployments = self.request(
            "api", "list deployments", "GET", f"{CONTROL}/api/v1/deployments", headers=self.headers(False),
        ).get("items", [])
        item = next((x for x in deployments if x.get("name") == DEPLOYMENT_NAME), None)
        if not item:
            item = self.request(
                "api", "create deployment", "POST", f"{CONTROL}/api/v1/deployments",
                headers=self.headers(), json={
                    "name": DEPLOYMENT_NAME, "asset_type": "AGENT", "asset_id": agent_id,
                    "asset_version_no": version_no, "rate_limit_per_minute": 60,
                },
            ).get("item")
        elif item.get("status") != "ACTIVE":
            item = self.request(
                "api", "activate deployment", "PATCH", f"{CONTROL}/api/v1/deployments/{item['deployment_id']}/status",
                headers=self.headers(), json={"status": "ACTIVE"},
            ).get("item")
        deployment_id = str(item.get("deployment_id") or "")
        key_data = self.request(
            "api", "auto generate temporary API key", "POST", f"{CONTROL}/api/v1/deployments/{deployment_id}/api-keys",
            headers=self.headers(), json={"name": "E2E 自动验收临时密钥", "expires_at": None},
        )
        self.api_key = str(key_data.get("secret") or "")
        api_key_id = str(key_data.get("item", {}).get("api_key_id") or "")
        self.state.update({
            "deployment_id": deployment_id, "deployment_slug": item.get("slug"),
            "temporary_api_key_id": api_key_id, "temporary_key_prefix": key_data.get("item", {}).get("key_prefix"),
        })
        self.save()
        if not self.api_key or not api_key_id:
            raise RuntimeError("API key generation did not return secret/id")
        return item, api_key_id

    def external_invoke(self, item: dict[str, Any], message: str, conversation_id: str | None, label: str) -> dict[str, Any]:
        started = time.perf_counter()
        response = requests.post(
            f"{CONTROL}{item['invoke_path']}",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "X-Trace-Id": str(uuid.uuid4())},
            json={"input": {"message": message}, "conversation_id": conversation_id, "stream": False}, timeout=180,
        )
        elapsed = (time.perf_counter() - started) * 1000
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text[:500]}
        ok = response.status_code == 200 and data.get("status") == "completed"
        row = {
            "label": label, "http_status": response.status_code, "elapsed_ms": round(elapsed, 2), "ok": ok,
            "conversation_id": data.get("conversation_id"), "run_id": data.get("run_id"),
            "output_chars": len(str(data.get("output") or "")),
            "citations_count": len(data.get("citations") or []),
            "metrics": data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
        }
        self.event(
            "api", label, elapsed, ok,
            http_status=response.status_code,
            output_chars=row["output_chars"],
            citations_count=row["citations_count"],
            runtime_total_ms=row["metrics"].get("total_ms"),
            first_token_ms=row["metrics"].get("first_token_ms"),
        )
        return row

    def run_external_api(self, agent_id: str, version_no: int) -> None:
        item, api_key_id = self.ensure_deployment_and_key(agent_id, version_no)
        rows: list[dict[str, Any]] = []
        try:
            first = self.external_invoke(item, "请基于知识库概括大模型企业落地的关键步骤。", None, "external single invoke")
            rows.append(first)
            second = self.external_invoke(item, "继续上一轮，给出第一步的验收指标。", first.get("conversation_id"), "external conversation follow-up")
            rows.append(second)
            if first.get("conversation_id") and second.get("conversation_id") != first.get("conversation_id"):
                self.issue(
                    "api", "CONVERSATION_NOT_REUSED",
                    "外部 API 接受 conversation_id，但返回了新的会话，发布 Agent API 当前不支持多轮会话复用",
                    first_conversation_id=first.get("conversation_id"), second_conversation_id=second.get("conversation_id"),
                )
            concurrent_started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = [
                    pool.submit(self.external_invoke, item, f"并发请求 {i + 1}：说明 RAG 的一个关键质量指标。", None, f"external concurrent {i + 1}")
                    for i in range(5)
                ]
                rows.extend(future.result() for future in concurrent.futures.as_completed(futures))
            concurrent_wall_ms = (time.perf_counter() - concurrent_started) * 1000
            self.results["external_api"] = {
                "rows": rows, "concurrent_wall_ms": round(concurrent_wall_ms, 2),
                "success_count": sum(1 for x in rows if x.get("ok")), "failure_count": sum(1 for x in rows if not x.get("ok")),
            }
        finally:
            if api_key_id:
                try:
                    self.request(
                        "api", "revoke temporary API key", "POST",
                        f"{CONTROL}/api/v1/deployment-api-keys/{api_key_id}/revoke",
                        headers=self.headers(), json={},
                    )
                    self.state["temporary_key_revoked"] = True
                    started = time.perf_counter()
                    revoked_response = requests.post(
                        f"{CONTROL}{item['invoke_path']}",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "X-Trace-Id": str(uuid.uuid4()),
                        },
                        json={"input": {"message": "revoked key verification"}, "stream": False},
                        timeout=30,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    rejected = revoked_response.status_code in {401, 403}
                    self.event(
                        "api", "verify revoked API key is rejected", elapsed, rejected,
                        http_status=revoked_response.status_code,
                    )
                    if not rejected:
                        self.issue(
                            "api", "REVOKED_KEY_STILL_ACCEPTED",
                            "已撤销的临时 API Key 仍可访问发布端点",
                            http_status=revoked_response.status_code,
                        )
                finally:
                    self.api_key = ""
                    self.save()

    def finish(self) -> None:
        elapsed_values = [float(x["elapsed_ms"]) for x in self.results["events"] if x.get("elapsed_ms") is not None]
        self.results["finished_at"] = utc_now()
        self.results["summary"] = {
            "event_count": len(self.results["events"]), "issue_count": len(self.results["issues"]),
            "request_p50_ms": percentile(elapsed_values, 0.5), "request_p95_ms": percentile(elapsed_values, 0.95),
            "request_mean_ms": round(statistics.mean(elapsed_values), 2) if elapsed_values else None,
        }
        self.save()

    def run(self, phase: str) -> None:
        self.login()
        kb_id = self.state.get("kb_id")
        agent_id = self.state.get("agent_id")
        version_no = int(self.state.get("agent_version_no") or 0)
        model_config: dict[str, Any] | None = None

        if phase in {"all", "knowledge"}:
            kb_id = self.ensure_knowledge_base()
            self.upload_files(kb_id)
            if phase == "knowledge":
                self.finish()
                return
        if not kb_id:
            raise RuntimeError("knowledge phase has not produced kb_id")

        if phase in {"all", "agent"}:
            agent_id, version_no, model_config = self.ensure_agent(kb_id)
            if phase == "agent":
                self.finish()
                return
        if not agent_id or not version_no:
            raise RuntimeError("agent phase has not produced a published agent")
        if model_config is None:
            agent = self.request(
                "agent", "load agent", "GET", f"{CONTROL}/api/v1/agents/{agent_id}", headers=self.headers(False),
            ).get("item", {})
            model_config = agent.get("model_config") or {}

        if phase in {"all", "chat"}:
            self.run_single_dialogue(kb_id, agent_id, model_config)
            if phase == "chat":
                self.finish()
                return
        if phase in {"all", "concurrency"}:
            self.run_concurrent_dialogues(kb_id, agent_id, model_config)
            if phase == "concurrency":
                self.finish()
                return
        if phase in {"all", "api"}:
            self.run_external_api(agent_id, version_no)
        self.finish()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Powflow complete Agent acceptance simulation")
    parser.add_argument("--phase", choices=["all", "knowledge", "agent", "chat", "concurrency", "api"], default="all")
    args = parser.parse_args()
    password = os.environ.get("POWFLOW_TEST_PASSWORD", "")
    if not password:
        print("POWFLOW_TEST_PASSWORD is required", file=sys.stderr)
        return 2
    runner = AcceptanceRunner(password)
    try:
        runner.run(args.phase)
        return 0
    except Exception as exc:
        runner.issue(args.phase, "RUN_ABORTED", str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
