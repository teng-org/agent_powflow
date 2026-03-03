# ADR-005：核心 Agent 能力优先落地（对话/历史/压缩记忆/工具调用）

- 状态：Accepted
- 日期：2026-02-15
- 决策方：产品 + 架构
- 适用范围：`agent_powflow` v1/v1.5
- 关联：
  - `docs/decisions/ADR-002-domain-boundary-and-canvas-execution.zh-CN.md`
  - `docs/decisions/ADR-003-tenant-isolation-v1.md`
  - `docs/decisions/ADR-004-runtime-and-queue-topology.md`
  - `docs/design/springboot-controlplane-chat-multitenant-plan-v1.zh-CN.md`

## 背景（Context）

当前知识库链路（上传、切片、嵌入、总结、任务治理）已具备较强工程能力，但用户侧“可持续对话体验”尚未形成闭环。  
在进入多租户企业交付前，必须先形成稳定的核心 Agent 能力，否则会出现：

1. 对话只能单轮，无法承载真实业务交互。
2. 长对话上下文膨胀导致成本与延迟不可控。
3. 缺少可控工具调用，无法完成“行动型”任务。
4. 会话状态与执行状态不可追踪，难以定位问题与治理成本。

因此，本 ADR 决定先落地“Agent Core”能力，再扩展复杂编排与高级治理。

## 决策（Decision）

## 1. 能力优先级（固定顺序）

v1 优先落地以下四类核心能力，按顺序推进，不并行扩散范围：

1. 对话链路（多轮 + 流式 + 取消）
2. 历史管理（会话与消息持久化）
3. 自动压缩与记忆（短期窗口 + 滚动摘要 + 长期事实）
4. 工具调用（白名单工具 + 参数校验 + 可观测）

只有上述四类能力达标后，才进入复杂编排框架（如 LangGraph）与高级代理行为。

## 2. 编排框架策略：先原生状态机，后可插拔升级

本期不将核心链路直接绑定到第三方图编排框架。  
采用“原生状态机 + 抽象接口”方式：

- 抽象：`ChatOrchestrator`（统一对话编排接口）
- 默认实现：`NativeOrchestrator`
- 预留实现：`LangGraphOrchestrator`（后续可接）

强约束：

1. 业务层不得直接依赖具体编排框架 SDK。
2. 编排状态机事件必须结构化并持久化（见第 6 节）。
3. 任何框架替换不得改变对外 API 与审计语义。

## 3. 运行拓扑：在线对话与后台重任务解耦

1. 用户对话主链路默认不走 Celery 队列（保持低延迟和流式实时性）。
2. 知识库重任务（切片/嵌入/重建/总结）继续走 worker 队列。
3. 对话运行资源与 KB 任务资源隔离（并发槽、限流、预算独立计数）。

说明：

- 该策略与 ADR-004 不冲突；ADR-004 约束的是异步任务语义。
- 对话链路可在后续将“子步骤”异步化，但不改变用户主体验。

## 4. 上下文与记忆策略（必须实现）

## 4.1 三层上下文模型

1. 短期记忆：最近 K 轮原始消息（窗口）
2. 中期记忆：会话滚动摘要（summary memory）
3. 长期记忆：可检索事实项（fact memory）

上下文构建顺序固定为：

1. `system instruction`
2. `conversation summary`
3. `recent messages`
4. `retrieved memory facts`
5. `retrieval evidence from KB`
6. `current user input`

## 4.2 自动压缩触发规则

满足任一条件触发压缩：

1. 预计上下文 token 超过阈值
2. 连续轮次超过阈值
3. 显式触发（管理员或策略）

压缩规则：

1. 只保留“事实、偏好、约束、待办决策”
2. 不保留噪音、客套、低置信片段
3. 生成的新摘要必须带版本号与更新时间

## 4.3 长期记忆写入规则

仅写入稳定信息，必须满足：

1. 可归因（来源消息/工具结果）
2. 可追踪（trace_id/run_id）
3. 可撤销（人工修正或删除）

禁止写入：

1. 临时状态、一次性操作指令
2. 高风险隐私（未授权）

## 5. 工具调用策略（必须可控）

## 5.1 工具治理

工具必须通过注册表管理，注册信息包括：

1. `tool_id`
2. `tenant_scope`
3. `input_schema`（JSON Schema）
4. `timeout/retry`策略
5. 权限要求（role/permission）

## 5.2 调用约束

每次 run 固定预算：

1. 最大工具调用次数
2. 单工具超时上限
3. 总工具执行时间上限

错误处理：

1. schema 校验失败 -> 不执行，返回结构化错误
2. timeout/retry 耗尽 -> 记录失败，允许模型回退回答
3. 非幂等工具默认禁止自动重试

## 5.3 安全要求

1. 工具调用前必须做租户与权限校验
2. 工具输入输出必须审计（脱敏后）
3. 外部网络工具默认白名单域名

## 6. 对话状态机与事件语义（统一标准）

## 6.1 Run 状态机

状态：

1. `QUEUED`
2. `RUNNING`
3. `SUCCEEDED`
4. `FAILED`
5. `CANCELLED`

阶段（stage）：

1. `AUTH`
2. `CONTEXT_BUILD`
3. `RETRIEVAL`
4. `TOOL_CALL`
5. `GENERATION`
6. `PERSIST`

## 6.2 事件协议（SSE / 结构化日志）

必须支持：

1. `run_started`
2. `retrieval_done`
3. `tool_call_started`
4. `tool_call_done`
5. `token`
6. `run_done`
7. `run_error`
8. `run_cancelled`

所有事件必须带：

1. `tenant_id`
2. `actor_id`
3. `trace_id`
4. `conversation_id`
5. `run_id`
6. `ts`

## 7. 数据模型（最小集）

必须具备以下实体（Control Plane 权威）：

1. `conversations`
2. `messages`
3. `chat_runs`
4. `memory_items`
5. `tool_calls`

最小字段要求：

1. 统一主键 + `tenant_id`
2. `created_at/updated_at`
3. `trace_id`（run/tool）
4. `status/stage/error`
5. token/cost/latency 指标字段

## 8. API 约束（最小闭环）

必须提供：

1. `POST /api/v1/chat/sessions`
2. `GET /api/v1/chat/sessions`
3. `GET /api/v1/chat/sessions/{id}/messages`
4. `POST /api/v1/chat/sessions/{id}/messages`
5. `GET /api/v1/chat/runs/{run_id}/stream`
6. `POST /api/v1/chat/runs/{run_id}/cancel`

禁止：

1. 通过 admin API 直接代替用户对话主链路
2. 跳过 run 持久化直接调用模型

## 9. 观测与成本治理（上线前硬要求）

必须采集：

1. p50/p95 延迟
2. 首 token 延迟
3. token 输入/输出/总量
4. 费用（人民币）估算
5. 工具调用成功率/超时率
6. 取消成功率

统计维度必须包含：

1. tenant
2. user
3. model
4. task_category（chat/tool/retrieval）

## 10. 验收标准（Definition of Done）

## 10.1 功能验收

1. 支持至少 30 轮连续对话，历史可回放。
2. 长对话自动压缩可生效，回答不明显退化。
3. 工具调用可执行、可追踪、可回退。
4. 用户可取消正在生成的 run，并最终落 `CANCELLED`。

## 10.2 稳定性验收

1. 单租户 50 并发会话不崩溃。
2. 取消成功率 >= 99%。
3. run 状态机无非法跃迁。

## 10.3 治理验收

1. 所有对话链路带完整上下文（tenant/actor/trace）。
2. token/cost 可按租户/用户/模型查询。
3. 审计日志可从用户输入追踪到模型输出与工具调用。

## 11. 影响（Consequences）

### 正向影响

1. 快速形成真实可用的 Agent 核心体验。
2. 将“能力建设”与“框架选型风险”解耦。
3. 为后续多租户和企业治理提供稳定基础。

### 代价

1. 需要先建设原生编排层，后续再做框架适配层。
2. 初期高级代理能力（复杂图分支）上线速度会稍慢。

## 12. 实施守卫（Guardrails）

1. 未实现 run 状态机与事件持久化，不得上线用户对话。
2. 未实现记忆压缩策略，不得开放长会话默认无限上下文。
3. 工具调用未做 schema/权限校验，不得对外开放。
4. 任意新对话能力 PR 必须提供 token/cost/latency 观测埋点。

