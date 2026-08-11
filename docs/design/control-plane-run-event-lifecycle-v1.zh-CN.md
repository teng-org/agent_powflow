# Control Plane Conversation / Run / Event 生命周期 v1

- 日期：2026-08-02
- 状态：已实现，并通过单元测试与 Compose 真实数据库冒烟

## 1. 目标与边界

Control Plane 是会话、消息、运行及运行事件的权威写入层。用户请求只引用已发布的 Workflow 版本；Execution Plane 通过受保护的内部事件接口回写进度和结果，不直接修改 Control Plane 数据库。

当前版本已经闭合“创建会话 → 发送用户消息 → 固化 Workflow 快照 → 同事务写入 Outbox → 租约保护的图执行 → 逐节点/终态回写 → 生成助手消息 → SSE/历史查询”的生命周期。事务 Outbox、执行租约/主动中断、Provider 业务幂等、核心 DAG/条件运行时和两类死信审计重放均已实现；工具、检索、并行和人工节点仍属于下一阶段边界。

## 2. 状态机与不变量

Run 状态为 `PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED`。

1. 合法主路径为 `PENDING → RUNNING → SUCCEEDED|FAILED|CANCELLED`；也允许尚未领取的 `PENDING → CANCELLED`。
2. 禁止跳过 RUNNING 直接成功或失败；终态不可再次迁移。
3. `progress` 只能在 0–100 内单调递增；成功态强制为 100。
4. 首次进入 RUNNING 时记录 `started_at`，进入任一终态时记录 `finished_at`。
5. Run 固化 `workflow_id + workflow_version_id + workflow_version_no + workflow_checksum`，运行期间不读取可变草稿。
6. 内部事件以 `tenant_id + external_event_id` 唯一，重复提交返回原事件并标记 `duplicate=true`。
7. 每个 Run 的 `sequence_no` 连续递增，数据库同时约束 `run_id + sequence_no` 唯一。
8. 成功事件持久化 output、citations、metrics，并且只生成一条关联该 Run 的 ASSISTANT 消息。
9. 会话按 `tenant_id + created_by` 隔离；存在活跃 Run 时拒绝删除会话。

## 3. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/api/v1/conversations` | 查询或创建当前用户会话 |
| GET / POST | `/api/v1/conversations/{conversationId}/messages` | 查询消息，或发送消息并创建 Run |
| DELETE | `/api/v1/conversations/{conversationId}` | 删除无活跃 Run 的会话及关联数据 |
| GET | `/api/v1/runs/{runId}` | 查询权威 Run 状态和结果 |
| POST | `/api/v1/runs/{runId}/cancel` | 用户取消待执行/执行中的 Run |
| GET | `/api/v1/runs/{runId}/events/history` | 查询持久化事件历史 |
| GET | `/api/v1/runs/{runId}/events` | SSE 重放历史并推送后续事件 |
| POST | `/api/v1/internal/runs/{runId}/events` | Execution Plane 幂等回写事件 |

内部接口不接受用户 JWT，使用 `X-Internal-Token` 与 `CONTROL_PLANE_INTERNAL_TOKEN` 做常量时间比较。生产环境仍应升级为短期服务凭证或 mTLS，并支持轮换和来源审计。

## 4. 数据模型

- `conversations`：租户、所有者、标题、状态与审计时间。
- `messages`：USER/ASSISTANT 内容、会话归属及关联 Run。
- `runs`：Workflow 不可变快照、状态机、输入输出、引用、指标、错误与生命周期时间。
- `run_events`：外部幂等键、Run 内序号、事件类型、状态、阶段、进度、载荷和来源服务。

并发写入通过 Run 行悲观锁、事件唯一约束和 JPA revision 共同保护。Control Plane 已由 Flyway V1–V5 管理完整 schema，Hibernate 使用 `ddl-auto=validate`，不再隐式建表。

## 5. SSE 语义

订阅时先按序重放持久化历史，再注册实时推送；SSE `id` 使用 `sequence_no`，`event` 使用业务事件类型。网络重连或重放/实时交界处可能看到重复事件，客户端必须按 SSE id 去重，最终事实以历史接口和 Run 详情为准。

## 6. 验证记录

- `RunServiceTest`：覆盖成功链路与助手消息、非法跳态、进度回退、事件幂等、活跃会话删除保护。
- Control Plane Maven：43/43 通过（含结构化并行 DSL、Workflow、Run 管理追踪、Outbox、Lease、DLQ 和真实 PostgreSQL 迁移）。
- `tools/smoke/stack-smoke.ps1`：在真实 PostgreSQL、RabbitMQ 和 Celery 下覆盖内部令牌拒绝、PENDING/RUNNING/SUCCEEDED、运行中取消、迟到结果抑制、显式失败、重复事件、输出/指标落库、消息生成约束、事件序列、SSE 历史重放与资源清理；进度回退 409 由 `RunServiceTest` 覆盖。
- 调试接口目录：230/230，覆盖率 100%，无重复 ID、无悬空路由。

## 7. 下一阶段

1. 为真实 Provider 和工具副作用增加业务幂等键，并支持租约丢失后的主动中断。
2. 把受限 mock Runtime 扩展为按 DSL 拓扑执行的节点运行时。
3. 在已完成的重试耗尽、取消/失败 E2E 上继续补 SSE 断线恢复和告警触发。
4. 将静态内部令牌升级为可轮换服务身份；为 SSE 增加 `Last-Event-ID` 恢复和心跳。
