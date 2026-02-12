# ADR-003：多租户隔离与系统上下文强约束（v1）

- 状态：Accepted
- 日期：2026-02-09
- 决策方：产品 + 架构
- 适用范围：`agent_powflow` v1
- 关联：
  - `docs/design/current-phase-construction-checklist-v1.zh-CN.md`
  - `docs/decisions/ADR-001-product-scope-v1.zh-CN.md`
  - `docs/decisions/ADR-002-domain-boundary-and-canvas-execution.zh-CN.md`

## 背景（Context）

当前施工清单已经隐含 `tenant-aware`、`trace-aware`、`Task/Run` 语义边界与治理前置校验。  
若这些约束不升级为 ADR 级硬规则，后续实现中容易被绕过，导致：

- 串租户（数据越权）
- 无法完整审计（链路断裂）
- 执行层绕过 License/Feature Flag
- Task 与 Run 混用引发语义混乱

## 决策（Decision）

## 1. 多租户隔离（MVP 固定 L1）

- 所有业务对象强制 `tenant_id`。
- 所有读写接口强制 tenant 过滤，不允许裸查询。
- 所有内部事件必须携带 `tenant_id`。
- 执行层不得自行推断 tenant，必须使用上游透传值。

## 2. 系统级上下文契约（System Context Contract）

所有跨服务调用、消息事件、日志记录必须携带：

- `tenant_id`
- `actor_id`（`user_id` 或 `system`）
- `trace_id`
- `source_service`

职责分工：

- Spring Control Plane：负责注入与校验。
- Python Execution Plane：只透传，不篡改。
- 任一字段缺失：请求/事件拒绝处理并记录审计日志。

## 3. 治理前置校验（不可绕过）

所有 Run / Task 在进入 Execution Plane 前，必须由 Control Plane 完成：

- License entitlement 校验
- Feature flag 决策

未通过治理校验的任务，禁止投递 RabbitMQ。

## 4. Task / Run 语义边界（不可混用）

- Task：系统级异步工作单元（无 UI 语义）
- Run：用户可感知的 Agent 执行单元（有业务语义）

强制映射：

- 文档入库归类为 Task
- 对话执行归类为 Run
- Run 内部可触发 Task（工具、索引等后台工作）

## 5. 数据模型强制字段

`runs` 与 `tasks` 必须包含：

- `source`（`user|system|tool|scheduler`）

用途：

- 审计归因
- 统计分析
- 执行回放

## 6. 可观测与验收硬指标

- 任一 run / task 必须可通过 `trace_id` 回溯完整执行路径。
- 审计日志必须可关联 `tenant_id + actor_id + trace_id`。
- 出现上下文字段缺失时，系统必须拒绝执行并可观测。

## 影响（Consequences）

### 正向影响

- 显著降低串租户和越权风险。
- 保证跨服务链路可审计、可排障、可回放。
- 阻断执行层绕过治理逻辑的工程风险。
- 保持 Task/Run 语义稳定，降低新人误用概率。

### 代价

- 需要在网关、API、消息中间件和日志层统一上下文字段处理。
- 对部分历史代码/脚本会产生改造成本。

## 实施守卫（Guardrails）

1. PR 检查项必须包含 tenant/context 字段覆盖。
2. 新增 API 若未声明 tenant/context 契约，不允许合并。
3. Worker 代码禁止构造或覆盖 `tenant_id/actor_id/trace_id`。
4. 测试必须覆盖“缺失上下文字段 -> 拒绝执行”的失败路径。
