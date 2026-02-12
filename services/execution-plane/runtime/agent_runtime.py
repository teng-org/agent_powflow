from pydantic import BaseModel, Field

from models.llm_provider import GenerateRequest, MockLlmProvider
from retrieval.schemas import SearchRequest
from retrieval.service import search


class AgentRunRequest(BaseModel):
    tenant_id: str
    actor_id: str = "system"
    trace_id: str
    source_service: str
    kb_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    allow_generation_without_evidence: bool = True


class AgentRuntime:
    def __init__(self) -> None:
        self.llm = MockLlmProvider()

    def run(self, req: AgentRunRequest) -> dict:
        evidence = search(
            SearchRequest(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                query=req.question,
                top_k=req.top_k,
            )
        )

        if not evidence and not req.allow_generation_without_evidence:
            return {
                "status": "FAILED",
                "stage": "retrieval",
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": "No evidence found for generation policy.",
                "trace_id": req.trace_id,
            }

        llm_result = self.llm.generate(
            GenerateRequest(
                tenant_id=req.tenant_id,
                question=req.question,
                evidence=evidence,
            )
        )
        return {
            "status": "SUCCEEDED",
            "stage": "output",
            "answer": llm_result["answer"],
            "low_evidence": llm_result["low_evidence"],
            "model_endpoint": llm_result["endpoint"],
            "citations": evidence,
            "metrics": {
                "prompt_tokens": max(1, len(req.question) // 4),
                "completion_tokens": max(1, len(llm_result["answer"]) // 4),
                "cost": 0.0,
            },
            "trace_id": req.trace_id,
        }


agent_runtime = AgentRuntime()
