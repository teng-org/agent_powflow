# runtime

Agent / Workflow 执行运行时引擎。

## 当前实现

- `agent_runtime.py`：
  - 输入问题
  - 调用 retrieval 获取 evidence
  - 调用 LLM provider 生成答案
  - 输出 citations + metrics + low_evidence

## 约束

- v1 固定策略：证据不足可生成，但标记 `low_evidence=true`。
- 失败策略与队列策略以 ADR-004 为准。
