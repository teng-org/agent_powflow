from pydantic import BaseModel

from models.ai_gateway import ai_gateway


class GenerateRequest(BaseModel):
    tenant_id: str
    question: str
    evidence: list[dict]


class MockLlmProvider:
    def generate(self, req: GenerateRequest) -> dict:
        if not req.evidence:
            return {
                "answer": "我没有检索到足够证据，先给出一般性回答：请补充更具体的业务上下文或上传相关文档。",
                "low_evidence": True,
                "endpoint": {"endpoint_id": "none", "provider": "none", "model": "none"},
            }

        evidence_lines = [
            f"[{idx + 1}] {item.get('source', 'unknown')}#{str(item.get('chunk_id', ''))[:8]}: {item.get('snippet', item.get('text', ''))}"
            for idx, item in enumerate(req.evidence[:4])
        ]
        messages = [
            {"role": "system", "content": "你是企业知识库问答助手，必须优先基于证据回答，表达简洁。"},
            {
                "role": "user",
                "content": (
                    f"问题：{req.question}\n"
                    "检索证据：\n"
                    + "\n".join(evidence_lines)
                    + "\n请给出回答，并保持可执行。"
                ),
            },
        ]
        resp = ai_gateway.chat_completion(
            tenant_id=req.tenant_id,
            messages=messages,
            pool="main",
            task="llm",
            strict=False,
            temperature=0.2,
            max_tokens=600,
        )
        answer = (resp.get("text") or "").strip()
        if not answer:
            answer = (
                "基于知识库检索结果，我的回答如下：\n"
                + "\n".join(evidence_lines[:3])
                + "\n总结：上述证据表明该问题应优先按检索到的业务规则执行。"
            )
        return {"answer": answer, "low_evidence": False, "endpoint": resp.get("endpoint", {})}

