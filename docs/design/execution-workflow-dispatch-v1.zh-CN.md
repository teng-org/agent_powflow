# Control → Execution Workflow 投递与图执行 v1

- 日期：2026-08-02
- 状态：事务 Outbox、执行租约/主动中断、Provider 业务幂等、重试/死信审计已闭环

## 1. 已实现链路

1. 创建 Run 时在同一事务写入 `run_dispatch_outbox`；调度器只领取 Outbox，不再扫描所有 `PENDING` Run。
2. 每次投递携带 Run 身份、租户/操作者、Workflow 版本 ID/版本号/checksum、不可变 DSL/layout 和输入。
3. Execution API 的 `/api/v1/internal/control-runs/dispatch` 校验内部令牌和 payload 后，以稳定 task id 投递到带 DLX 的 RabbitMQ `control_run_queue`。
4. Celery Worker 发送确定性的 `RUN_STARTED` 幂等事件，按 DAG 执行 Workflow，逐节点回写事件，再回写 `RUN_SUCCEEDED` 或 `RUN_FAILED`。
5. Control Plane 接收结果后更新权威 Run、生成 ASSISTANT 消息，并向 SSE 订阅者推送。

投递为 at-least-once：Execution API 或 RabbitMQ 暂时不可用时，Outbox 按指数退避重试；耗尽后进入可查询、可重放的 `DEAD_LETTER`。Control Plane 不会把“HTTP 已接受”误当成执行成功。

## 2. 重复、重试与取消

- 调度 task id 固定为 `control-run-{run_id}`。
- Control Run 明确使用 `ignore_result`，完成状态只以 Control Plane 事件为准，避免 Celery RPC backend 堆积无人消费的结果消息。
- Worker 事件 ID 固定为 `execution:{run_id}:started|succeeded|failed`；Control Plane 唯一约束负责最终幂等。
- 普通重复任务看到已存在的 started 事件后直接返回；Celery retry/redelivery 可恢复回调暂时失败的任务。
- Worker 在生成结果后重新读取执行上下文；若用户已取消，则不再提交成功事件。
- Control Plane 只领取 PENDING/RETRY Outbox；Run 取消会同步取消未发布投递，终态拒绝执行租约与新状态迁移。

Worker 必须取得 Control Plane 执行租约并持续心跳；并发消息不能抢占活跃租约，过期租约可由后续 attempt 接管。真实 Provider 通过持久化 invocation key、请求 hash、结果缓存和 `Idempotency-Key` 抑制付费重复调用，但整体仍不宣称严格 exactly-once。

## 3. Workflow Graph Runtime

`runtime/workflow_runtime.py` 当前只接受 `start`、`llm.generate`、`end`，未知节点 fail closed。`WORKFLOW_PROVIDER_MODE=mock` 返回可复现的本地答案；`api_strict` 调用真实 OpenAI-compatible Provider，禁止 mock 降级并启用业务幂等账本。

开发 Compose 同时使用 `EMBEDDING_MODE=local_only` 与 `CHAT_PROVIDER_MODE=mock`：文档和查询使用确定性本地向量；Chat 的流式/非流式生成与会话向量也完全本地执行。生产环境可显式切回 `api_strict`。本地 embedding 已从 API Gateway 解耦，确保 `local_only` 不会先解析或调用外部端点。

文本入库的 `auto_summary` 改为显式 opt-in，默认 `false`；这避免“本地 embedding”路径在后台总结阶段意外调用真实付费 LLM。

Runtime 已按 DSL 边拓扑执行 `start`、`condition.if`、`llm.generate`、`end`，支持条件路径与多个 LLM 串联。后续仍需逐项加入检索、工具、并行/汇聚、人工审批、模板和结构化输出。

## 4. 安全边界

Control 与 Execution 内部端点都使用 `X-Internal-Token`，服务端通过常量时间比较校验。令牌由 Compose 环境注入，不在冒烟输出中打印。生产环境应迁移到可轮换短期凭证或 mTLS，并限制服务网络入口。

## 5. 验证

- Execution 容器 unittest：20/20（含图 Workflow、Provider 幂等/中断、本地 embedding、执行租约、最终任务死信）。
- Chat Plane test：31/31（认证 6、mock 流式/向量 2、显式 PostgreSQL 迁移 9、Run/SSE 生命周期 8、Provider 故障沙箱 6）。
- Control Plane Maven：43/43（含结构化并行 DSL、Workflow、Run 管理追踪、Outbox、Lease、DLQ 和真实迁移）。
- Endpoint catalog：230/230、100%。
- Stack smoke：除原有成功/取消/失败、检索 Chat 链路外，验证双分支图选择、8 条节点事件、3 条事务 Outbox 发布、活跃租约互斥与终态租约拒绝。
- Reliability smoke：真实停止 Execution API，验证投递重试耗尽 → 数据库死信 → 管理员重放 → Run 恢复成功。
- DLQ audit smoke：RabbitMQ 执行死信 → 权威持久化 → Console API 查询 → 重放 → 三步审计闭环。
- 所有持久化任务均以 PostgreSQL 状态为准并设置 `ignore_result`，避免 Celery RPC backend 产生无人消费的结果队列。

## 6. 尚未完成

1. 本地真实 HTTP Provider/Tool 故障沙箱已完成；继续使用第三方测试账号验收供应商限流头、计费/熔断与幂等支持。
2. DLQ 备注、批量操作和告警；浏览、授权重放、忽略和审计已实现。
3. 扩展 Tool、Retrieval、并行/汇聚和人工审批节点；核心 DAG、条件和逐节点事件已实现。
4. 告警触发和 SSE 断线续传的负向全栈 E2E；重试耗尽、取消竞态与显式失败已覆盖。
