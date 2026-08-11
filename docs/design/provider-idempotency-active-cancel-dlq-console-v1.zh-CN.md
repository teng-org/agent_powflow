# Provider 幂等、主动中断与执行 DLQ Console v1

- 日期：2026-08-03
- 状态：已实现并通过全栈验证

## 1. Provider 调用语义

`WORKFLOW_PROVIDER_MODE` 支持 `mock` 和 `api_strict`。`api_strict` 只接受真实 OpenAI-compatible 端点，不允许 mock 静默降级。

每个 `llm.generate` 生成稳定调用键：

```text
sha256(tenant_id + run_id + workflow_checksum + node_id + operation)
```

请求正文另做 canonical hash。`provider_invocations` 的行为是：

- 第一次调用取得有期限的 `IN_PROGRESS` claim；
- 相同 key、不同请求 hash 立即冲突，防止重试期间语义漂移；
- live claim 拒绝并发重复调用；过期或失败 claim 可由后续 attempt 接管；
- `SUCCEEDED` 保存完整 Provider 响应，Celery 重试直接使用缓存；
- 请求同时携带 `Idempotency-Key`，让支持该协议的供应商在网络不确定窗口继续去重。

## 2. 租约丢失后的主动中断

`ExecutionLeaseGuard` 将心跳拒绝、终态和本地过期统一转换为 abort signal。Workflow 的 mock 等待和真实 Provider HTTP 调用都接收同一 `should_cancel` 回调；HTTP watchdog 每 100ms 检查一次，触发后关闭 client，使在途 I/O 尽快退出。用户取消产生 `RUN_TERMINAL` 时返回 cancelled；租约被其他 Worker 接管或 Control Plane 长期不可达时继续走受控 Celery 重试，不把租约故障误写为业务失败。

主动关闭属于 cooperative/best-effort cancellation：已经到达且不支持幂等键的外部供应商仍可能完成请求，因此持久化 invocation 账本和供应商幂等头不能省略。

## 3. 执行死信闭环

```text
ControlRunTask final failure
  -> RabbitMQ control_run_dead_letter
  -> DLQ ingest task
  -> POST /api/v1/internal/run-dead-letters
  -> run_dead_letters + INGESTED audit
  -> Console authorized replay
  -> original run_dispatch_outbox = PENDING
  -> terminal Run callback
  -> REPLAYED + REPLAY_COMPLETED audit
```

内部写入以 `failed_task_id` 唯一，重复投递返回原记录。管理员接口严格按 JWT tenant 隔离：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/internal/run-dead-letters` | Worker 幂等入库 |
| GET | `/api/v1/admin/run-dead-letters` | 状态筛选列表 |
| GET | `/api/v1/admin/run-dead-letters/{id}` | 详情与原始载荷 |
| GET | `/api/v1/admin/run-dead-letters/{id}/audit` | 审计时间线 |
| POST | `/api/v1/admin/run-dead-letters/{id}/replay` | 授权单条重放 |
| POST | `/api/v1/admin/run-dead-letters/{id}/dismiss` | 审计后忽略 |

Console 的“运行死信”页支持响应式表格、刷新/空态/错误态、详情载荷、审计轨迹、重放/忽略确认与操作中反馈。

## 4. 验证

- Control Plane 33/33；Execution 容器 20/20；Console 10/10；生产构建通过。
- Endpoint catalog 230/230，无重复、无悬空。
- Flyway v3 已在 PostgreSQL 16 实例应用。
- 标准 stack smoke、Outbox reliability smoke、DLQ audit smoke 全部通过。
