from pydantic import BaseModel

from models.api_pool import api_pool_manager


class GenerateRequest(BaseModel):
    tenant_id: str
    question: str
    evidence: list[dict]


class MockLlmProvider:
    """MVP mock provider. Replace with OpenAI/local model adapter later."""

    def generate(self, req: GenerateRequest) -> dict:
        endpoint = api_pool_manager.get_default_for_tenant(req.tenant_id, task="llm")
        endpoint_info = {
            "endpoint_id": endpoint.endpoint_id if endpoint else "none",
            "provider": endpoint.provider if endpoint else "none",
            "model": endpoint.model if endpoint else "none",
        }

        if not req.evidence:
            return {
                "answer": "我没有检索到足够证据，先给出一般性回答：请补充更具体的业务上下文或上传相关文档。",
                "low_evidence": True,
                "endpoint": endpoint_info,
            }

        evidence_lines = [
            f"[{idx + 1}] {item.get('source', 'unknown')}#{item.get('chunk_id', '')[:8]}: {item.get('snippet', '')}"
            for idx, item in enumerate(req.evidence[:3])
        ]
        answer = (
            "基于知识库检索结果，我的回答如下：\n"
            + "\n".join(evidence_lines)
            + "\n总结：上述证据表明该问题应优先按检索到的业务规则执行。"
        )
        return {"answer": answer, "low_evidence": False, "endpoint": endpoint_info}
