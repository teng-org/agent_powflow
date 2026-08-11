# runtime

Agent / Workflow 执行运行时引擎。

## 当前实现

- `agent_runtime.py`：
  - 输入问题
  - 调用 retrieval 获取 evidence
  - 调用 LLM provider 生成答案
  - 输出 citations + metrics + low_evidence
- `workflow_runtime.py`：按发布快照执行 DAG，发出节点事件，并支持取消/失租中断以及 `human.approval` checkpoint 暂停/恢复。
- `node_adapters.py`：统一注册 `start`、`condition.if`、`llm.generate`、`retrieval.search`、`tool.http`、`end`；`human.approval` 由运行时作为控制节点处理。
- `workflow_secrets.py`：提供租户隔离的 Secret 加密存储、内容轮换、禁用、删除、主/旧 Fernet 密钥兼容读取和运行时解析。
- `workflow_secret_rotation.py`：默认 dry-run；在事务 advisory lock + 表写锁下校验全部密文/指纹并批量重加密，失败整体回滚。
- `node_retry.py`：解析节点显式重试策略，执行故障分类、指数退避和硬失败拒绝。

## 约束

- v1 固定策略：证据不足可生成，但标记 `low_evidence=true`。
- 失败策略与队列策略以 ADR-004 为准。
- Workflow 节点统一通过 `node_adapters.py` 注册和执行；`retrieval.search` 强制 tenant 权限解析，`tool.http` 默认拒绝所有目标。
- `safe_http.py` 负责主机 allowlist、DNS/peer 地址校验、禁重定向、响应上限和主动取消。
- HTTP 认证只能通过 `auth.secret_ref` 引用同租户 Secret，并且只允许注入 `Authorization` 或 `X-Api-Key`。明文不得进入 DSL、事件、节点记录或幂等请求哈希。
- Secret Vault 依赖合法的 `WORKFLOW_SECRET_MASTER_KEY`；默认 Compose 留空并 fail closed。轮换窗口可通过 `WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS` 读取旧密文，新写入始终只使用主密钥。
- 主密钥轮换顺序固定为：新主/旧辅双读部署 → dry-run → 原子重加密 → 主密钥单独复验 → 清空旧密钥；不得跳过 PostgreSQL 备份与轮换后 Workflow Smoke。
- Control Plane 把节点事件同步为 `run_node_executions` 权威记录；旧 Run 可由 Console 从事件回退重建。
- `llm.generate`、`retrieval.search`、`tool.http` 可通过 `config.retry` 开启最多 5 次尝试；默认只执行一次。取消、失租、Secret/SSRF 策略错误和幂等冲突永不重试。
- 开启重试后会发出 `NODE_ATTEMPT_*` 事件，Control Plane 通过 `run_node_attempts` 保存每次尝试；Run metrics 提供 `node_attempt_count` 和 `node_retry_count`。
- `human.approval` 必须包含 `approve`/`reject` 两条出边，v1 禁止位于并行分支。恢复时继承 checkpoint 进度与历史，Run 进度不得回退。
