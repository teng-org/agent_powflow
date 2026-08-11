# Workflow 节点适配器、Retrieval 与安全 HTTP v1

- 日期：2026-08-03
- 状态：已实现并通过 Compose 全栈与故障演练验证

## 1. 目标与边界

本阶段把 Workflow Runtime 从按节点类型扩大的条件分支，收敛为统一节点适配器协议，并开放两种可发布、可执行节点：`retrieval.search` 与 `tool.http`。

本版本已增加租户级 Secret Vault 与受控认证头，但仍不允许任意敏感请求头、重定向跟随、默认内网访问或并行节点。HTTP 工具默认拒绝所有目标；只有运维显式配置主机 allowlist 后才可能发起请求。Secret 只以引用 ID 进入 DSL，明文不会写入版本快照、事件、幂等请求哈希或管理 API 响应。

## 2. 统一执行协议

`runtime/node_adapters.py` 定义：

- `NodeExecutionContext`：Run 快照、共享状态、Provider 模式、取消信号、幂等存储、租户 Secret 解析器和可注入外部依赖；
- `NodeExecutionResult`：状态增量、最小化追踪详情、引用、Provider/Tool 调用与缓存指标；
- `NodeAdapterRegistry`：节点类型到执行器的统一运行时注册入口；
- Adapter 只负责节点语义；图遍历、分支选择、进度和事件仍由 Workflow Runtime 负责。

运行状态不会写入节点事件。事件仅包含节点 ID、类型、序号、进度和经过裁剪的结果摘要。

## 3. `retrieval.search`

| 字段 | 必需 | 约束 | 默认值 |
| --- | --- | --- | --- |
| `kb_id` | 是 | 非空字符串，可用完整状态引用 `${kb_id}` | 无 |
| `top_k` | 否 | 1–20 | 5 |
| `query_field` | 否 | 非空状态路径 | `answer`，不存在时回退 `text` |
| `output_field` | 否 | 非空字符串 | `evidence` |
| `doc_ids` | 否 | 非空字符串数组 | 全部文档 |

安全与数据语义：

1. 使用 Run 的 `tenant_id` 解析知识库访问权限；
2. 只在 owner、显式绑定或 `SHARED_ALL` 命中时解析到真实 owner tenant；
3. 检索复用现有 embedding、Qdrant、SQL 冻结与脏向量过滤链路；
4. 输出标准引用包含 `kb_id/doc_id/chunk_id/chunk_index/source/score/text`；
5. Run metrics 记录引用数量，节点事件只记录 KB ID、结果数和查询字段，不记录查询或证据正文。

## 4. `tool.http`

### 4.1 DSL 配置

| 字段 | 必需 | 约束 |
| --- | --- | --- |
| `url` | 是 | 非空字符串；运行时仅允许策略许可的 HTTP(S) URL |
| `method` | 否 | `GET/POST/PUT/PATCH/DELETE`，默认 `GET` |
| `headers` | 否 | 对象；只允许非敏感请求头 |
| `query` | 否 | 对象 |
| `body` | 否 | 对象 |
| `timeout_seconds` | 否 | 1–60，且受运维上限约束 |
| `output_field` | 否 | 默认 `tool_response` |
| `auth.secret_ref` | 否 | Secret Vault 中同租户的 Secret ID；禁止在 DSL 中写明文凭据 |
| `auth.header` | 否 | 仅允许 `Authorization` 或 `X-Api-Key` |
| `auth.prefix` | 否 | `Authorization` 默认 `Bearer `，`X-Api-Key` 默认空字符串 |

字符串值为完整 `${state.path}` 时会从运行状态取值。URL 查询串不会进入节点追踪详情。

### 4.2 默认拒绝与 SSRF 防护

`runtime/safe_http.py` 强制：

- `WORKFLOW_HTTP_ALLOWED_HOSTS` 为空时拒绝所有主机；支持精确主机和 `*.example.com` 子域规则；
- 默认只允许 HTTPS；HTTP 必须显式设置 `WORKFLOW_HTTP_ALLOW_HTTP=true`；
- URL 禁止嵌入用户名、密码和 fragment；
- 请求前解析全部 DNS 地址并拒绝非公网地址；响应后再次解析，并校验实际网络 peer（可获取时）；
- 默认拒绝 loopback、私网、链路本地、保留、多播和未指定地址；开发环境放开需单独设置 `WORKFLOW_HTTP_ALLOW_PRIVATE_NETWORKS=true`；
- 不跟随重定向，3xx 直接失败；
- 只允许 `Accept`、`Content-Type`、`User-Agent`、请求/关联 ID 和 Runtime 注入的 `Idempotency-Key`；禁止 Authorization、Cookie 等敏感头；
- 超时上限默认 30 秒；响应按流读取，默认超过 1 MiB 即中止；
- 取消或失租时主动关闭在途 HTTP client。

### 4.3 幂等

所有 HTTP 节点调用使用 `tenant + run + workflow checksum + node + operation` 生成稳定 invocation key，并复用 `provider_invocations` ledger。成功结果可在 Worker 重投时复用；请求语义变化会触发哈希冲突。认证参与请求哈希的仅有 Secret ID、指纹、版本、头名和前缀，不包含明文。`POST/PUT/PATCH/DELETE` 自动携带相同的 `Idempotency-Key`，便于下游共同去重。

### 4.4 Secret Vault

- `workflow_secrets` 按 tenant 隔离，使用 `WORKFLOW_SECRET_MASTER_KEY` 驱动的 Fernet 加密存储；主密钥缺失或非法时创建、轮换和解析均 fail closed；
- 管理接口只返回名称、状态、版本、指纹和审计时间，不返回明文或密文；
- 支持创建、轮换、禁用和删除；删除前必须禁用；轮换后版本递增并重新启用；
- Worker 只在发起已通过网络策略检查的请求前把 Secret 注入受信认证头，调用结束后不持久化明文；
- 当前不支持主密钥在线轮换；替换主密钥前必须先设计重加密迁移，否则历史 Secret 将无法解密。

## 5. 发布校验与可观测性

Control Plane 注册表与 Runtime 同步支持六类节点：`start`、`condition.if`、`llm.generate`、`retrieval.search`、`tool.http`、`end`。发布前校验 Retrieval/HTTP 配置类型与数值边界；运行时再次校验图和网络策略。

新增 metrics：`tool_call_count`、`tool_cache_hits`、`retrieval_citation_count`。既有 Provider、路径、耗时和兼容图指标继续保留。

Control Plane 将 `NODE_STARTED/NODE_COMPLETED/NODE_FAILED` 同步为 `run_node_executions` 权威记录，保存节点类型、序号、状态、起止时间、耗时、裁剪详情和错误摘要。Console 优先读取 `/api/v1/admin/runs/{runId}/nodes`；旧 Run 没有节点记录时才从事件回退重建。

## 6. 验证基线

- Execution unittest：83/83；PostgreSQL/MinIO 集成用例要求显式测试连接，容器与 CI 验证不应跳过；真实 HTTP Tool 沙箱覆盖 429/401/断连/超时与幂等重放；文档三存储、统一告警/Prometheus、对象存储治理和并行运行时回归已覆盖；
- Control Plane Maven：43/43；
- Console Vitest：23/23；
- Endpoint catalog：230/230；
- Stack smoke 已通过真实 KB 入库后的 `retrieval.search` Run，以及未配置 allowlist 时 `tool.http` 默认拒绝 Run；
- Outbox reliability 与 DLQ audit smoke 复跑通过；
- HTTP 单测覆盖 allowlist、私网拒绝、DNS 二次校验、敏感头拒绝、流式大小上限和主动取消。

## 7. 后续工作

1. 本地真实 HTTP Provider/Tool 沙箱已覆盖重试、限流、断连和响应丢失窗口；继续用第三方测试账号验证供应商特定契约；
2. 并行 fan-out/join 和失败聚合语义已完成；后续扩展跨 Worker/嵌套并行；
3. 人工审批的持久化暂停/恢复已完成；
4. 增加节点配置的 Console 可视化编辑器与表单级策略提示；
5. Workflow 主密钥轮换/重加密和 JWT 无停机轮换已完成；外部 KMS/HSM 仍待接入。
