from pydantic import BaseModel, Field

from models.llm_provider import GenerateRequest, MockLlmProvider
from retrieval.schemas import SearchRequest
from retrieval.service import search_with_meta


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
    rank_method: str = Field(default="hybrid", pattern="^(vector|hybrid|rrf|keyword)$")
    context_window: int = Field(default=1, ge=0, le=2)
    rerank_enabled: bool = False
    rerank_k: int = Field(default=30, ge=1, le=100)
    rerank_required: bool = False
    allow_generation_without_evidence: bool = True


class AgentRuntime:
    def __init__(self) -> None:
        self.llm = MockLlmProvider()

    def run(self, req: AgentRunRequest) -> dict:
        retrieval_result = search_with_meta(
            SearchRequest(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                query=req.question,
                top_k=req.top_k,
                final_k=req.top_k,
                candidate_k=max(req.top_k * 4, 20),
                rank_method=req.rank_method,
                context_window=req.context_window,
                rerank_enabled=req.rerank_enabled,
                rerank_k=req.rerank_k,
                rerank_required=req.rerank_required,
            )
        )
        evidence = retrieval_result["evidence"]

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
            "retrieval": retrieval_result.get("meta") or {},
            "metrics": {
                "prompt_tokens": max(1, len(req.question) // 4),
                "completion_tokens": max(1, len(llm_result["answer"]) // 4),
                "cost": 0.0,
            },
            "trace_id": req.trace_id,
        }


agent_runtime = AgentRuntime()
