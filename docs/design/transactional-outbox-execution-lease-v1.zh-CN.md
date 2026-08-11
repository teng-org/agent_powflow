# Run 事务 Outbox、执行租约与死信 v1

- 日期：2026-08-02
- 状态：已实现，并通过 PostgreSQL/RabbitMQ/Celery 全栈故障验证

## 1. 目标

本设计解决旧链路的三个可靠性缺口：Run 提交后没有事务投递事实、重复 Celery 消息可能并发执行、投递失败没有有界重试和可运维死信。

核心语义仍是 **at-least-once delivery + lease-guarded execution**，不宣称跨数据库、RabbitMQ 和外部 Provider 的严格 exactly-once。真实 LLM 节点已增加业务幂等账本与供应商幂等头，语义为“同一节点调用成功结果最多生成一次；不确定的网络失败仍按供应商是否支持幂等键决定最终效果”。

## 2. 事务 Outbox

创建用户消息、Run、`RUN_CREATED` 事件和 `run_dispatch_outbox` 记录位于同一个 Control Plane 数据库事务。任一写入失败会整体回滚，不再通过扫描所有 `PENDING` Run 猜测待投递任务。

Outbox 状态：

```text
PENDING ──claim──> IN_FLIGHT ──HTTP accepted──> PUBLISHED
                        │
                        └──failure──> RETRY ──claim──> IN_FLIGHT
                                         │
                                         └──attempt exhausted──> DEAD_LETTER

PENDING / RETRY / IN_FLIGHT ──Run cancelled──> CANCELLED
DEAD_LETTER ──admin replay──> PENDING
```

- 领取使用 PostgreSQL `FOR UPDATE SKIP LOCKED`，允许多个 Control Plane 实例并行调度而不重复领取同一行。
- `locked_by + locked_until` 使调度实例崩溃后可以自动重新领取。
- 每次领取增加 `attempt_count`；失败按 `base × 2^(attempt-1)` 退避，并受最大间隔限制。
- 达到 `max_attempts` 后进入 `DEAD_LETTER`，Run 保持 `PENDING`，但显示 `DISPATCH_DEAD_LETTER`、`DISPATCH_RETRY_EXHAUSTED`，避免破坏终态不可逆约束。
- 管理员重放会清空错误、重置 attempt 并重新进入 `PENDING`；终态 Run 不允许重放。
- 已有 `PENDING` Run 在应用启动时做一次兼容性 backfill；新 Run 不依赖扫描。

运维接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/admin/run-dispatches?status=DEAD_LETTER` | 按租户查询最近 100 条投递记录 |
| POST | `/api/v1/admin/run-dispatches/{outboxId}/replay` | 重放死信投递 |

## 3. 执行租约

Celery Worker 收到任务后必须先调用 Control Plane 领取 `run_execution_leases`：

1. Control Plane 锁定 Run，再检查当前租约。
2. 活跃租约存在时，其他 Worker 得到 `LEASE_HELD`，不得执行 Workflow。
3. 租约不存在、已释放或已过期时，新 Worker 取得随机、不透明的 `lease_token`，`attempt_no` 递增。
4. Worker 后台线程按服务端返回的间隔发送心跳；连续不可达直到本地租约期限时 fail closed，并立即设置中断信号。
5. mock 延迟会在 50ms 粒度检查中断；真实 HTTP Provider 调用由 watchdog 关闭 `httpx.Client`，主动终止可中断的在途请求。
6. Run 成功、失败、取消时 Control Plane 主动释放租约；Worker `finally` 再做一次幂等释放。
7. 终态 Run 永远返回 `RUN_TERMINAL`，不能重新取得租约。

内部接口：

| 方法 | 路径 |
| --- | --- |
| POST | `/api/v1/internal/runs/{runId}/lease/claim` |
| POST | `/api/v1/internal/runs/{runId}/lease/heartbeat` |
| POST | `/api/v1/internal/runs/{runId}/lease/release` |

## 4. 重试与死信

- Control → Execution HTTP 投递：由数据库 Outbox 做有界指数退避；耗尽后进入可查询、可重放的数据库死信。
- Execution 回调或租约暂时不可达：Celery `execute_control_run` 最多重试 3 次。
- Worker 最终异常：`ControlRunTask.on_failure` 将诊断信息和原始执行载荷发布到持久化 RabbitMQ `control_run_dead_letter`。
- Worker 的独立 DLQ handler 消费该队列，并通过内部令牌幂等写入 Control Plane `run_dead_letters`；回调失败最多指数退避重试 8 次。
- Console 提供状态筛选、详情、原始载荷、审计时间线和带确认的授权重放；重放将原 Run 的 Outbox 恢复为 `PENDING`，终态回写后自动标记 `REPLAYED`。
- Workflow 自身的确定性业务失败仍通过 `RUN_FAILED` 落到权威 Run，不伪装成基础设施重试。

## 5. 数据库迁移和配置

Flyway v2 创建 Outbox 与租约；v3 创建 `provider_invocations`、`run_dead_letters`、`run_dead_letter_audit` 及状态约束和索引。已有非空开发库使用 `baseline-on-migrate` 记录 version 1 后顺序应用迁移。

关键配置：

- `CONTROL_PLANE_EXECUTION_DISPATCH_BATCH_SIZE`
- `CONTROL_PLANE_EXECUTION_DISPATCH_MAX_ATTEMPTS`
- `CONTROL_PLANE_EXECUTION_DISPATCH_CLAIM_SECONDS`
- `CONTROL_PLANE_EXECUTION_DISPATCH_RETRY_BASE_SECONDS`
- `CONTROL_PLANE_EXECUTION_DISPATCH_RETRY_MAX_SECONDS`
- `CONTROL_PLANE_EXECUTION_LEASE_DURATION_SECONDS`
- `CONTROL_PLANE_EXECUTION_LEASE_HEARTBEAT_SECONDS`

## 6. 验证证据

- Control Plane Maven：43/43，含结构化并行/DSL fail-closed、管理员追踪、Outbox、Lease、DLQ 持久化、重放、忽略和真实迁移测试。
- Execution 容器 unittest：20/20，含图执行、Provider 幂等键、主动中断、租约守卫和最终任务死信。
- Endpoint catalog：230/230，覆盖率 100%。
- 常规 stack smoke：3 个 Run 均创建并发布事务 Outbox；并发 Worker 无法抢占活跃租约；终态无法重新领取；成功/取消/失败均通过。
- `outbox-reliability-smoke.ps1`：停止 Execution API → 强制最后一次投递失败 → `DEAD_LETTER` → 管理员重放 → Run 成功，且严格清理。
- `dlq-audit-smoke.ps1`：RabbitMQ DLQ → Worker ingest → 管理 API 查询 → 重放 → Run 成功 → 三步审计闭环，且严格清理。
- Flyway schema version 为 3；`control_run_queue` 的 DLX 参数和 DLQ consumer 已在 RabbitMQ/Celery 实例核验。

## 7. 后续边界

1. 将业务幂等与主动中断扩展到未来的 HTTP Tool、写操作 Tool 和流式 Provider 适配器。
2. 增加真实 Provider 沙箱契约测试，验证供应商确实接受并遵守 `Idempotency-Key`。
3. 为 DLQ 增加备注、批量操作和指标告警；当前已覆盖浏览、单条重放、忽略和审计。
4. Control Plane 核心旧表已纳入 Flyway V1 baseline，并关闭 `ddl-auto=update`；后续 schema 变更只追加迁移。
