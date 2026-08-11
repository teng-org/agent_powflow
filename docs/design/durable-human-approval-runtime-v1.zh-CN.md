# 持久化人工审批运行时 v1

- 状态：已实现并完成全栈验收
- 最近验证：2026-08-03
- 范围：Control Plane、Execution Plane、Powflow Console、数据库迁移与接口目录

## 1. 目标与边界

工作流可在 `human.approval` 节点持久化暂停，进程或 Worker 重启后仍能由授权人员审批，并沿 `approve` 或 `reject` 分支继续执行。v1 明确不支持把人工审批放入 `parallel.fork/join` 分支；发布校验和运行时均采用 fail closed。

## 2. DSL 契约

审批节点必须恰好包含两条出边，标签分别为 `approve`、`reject`。配置约束：

- `title`：1–160 字符；`instructions`：最多 2000 字符；
- `assignee_roles`：1–8 个角色，角色名只允许字母、数字和下划线；
- `timeout_seconds`：60–604800；
- `timeout_action`：`approve` 或 `reject`。

Control Plane 发布校验与 Execution Runtime 使用同一组语义约束，避免绕过发布接口后静默执行错误图。

## 3. 状态与数据流

1. Runtime 到达审批节点，写入节点开始/等待事件，并生成不含公开响应的 checkpoint。
2. Worker 调用内部挂起接口；Control Plane 在同一事务内写入 `run_approvals`、把 Run/Node 标记为 `WAITING_APPROVAL` 并释放执行租约。
3. Console 只向具有分配角色的当前租户用户展示审批；平台管理员可覆盖角色限制。
4. 决定写入采用 revision 乐观锁；同一决定重复提交返回幂等结果，冲突决定被拒绝。
5. Control Plane 将同一 Run 重新放入事务 Outbox；Worker 领取新租约，恢复 checkpoint，并从审批节点沿决定分支继续。
6. 恢复执行沿用已完成节点、Provider/Citation/统计和进度；事件 ID 带执行 attempt，Run 进度不得回退。

超时扫描由 `CONTROL_APPROVAL_TIMEOUT_SCAN_ENABLED` 控制，按节点配置自动做出决定并进入同一恢复路径。取消等待中的 Run 会同步取消待审批记录。

## 4. 权限、安全与审计

- 所有查询和决定均强制租户隔离；普通用户还需命中 `assignee_roles`。
- checkpoint 只通过内部执行上下文返回，不进入公开审批 DTO。
- 内部挂起请求必须携带有效租约 token，过期或非持有者请求拒绝。
- `APPROVAL_REQUESTED`、`APPROVAL_DECIDED`、节点等待、恢复开始和最终状态均进入 Run 事件历史。
- 拒绝必须填写原因；原因最多 1000 字符。

## 5. 前端交互

Powflow Console 增加审批工作台，支持状态筛选、待办列表、审批详情、角色/超时信息、Run/Node/Event 审计，以及批准/拒绝的二次确认。拒绝原因必填，提交携带 revision 并明确展示并发冲突或过期结果。页面适配窄屏并遵循 `prefers-reduced-motion`。

运营摘要通过 `GET /api/v1/approvals/stats?due_within_seconds=900` 提供队列健康、待处理、临期、逾期、最老等待时长、24 小时决定/超时数、超时率和平均处理时长。统计与列表在数据库侧使用同一租户和角色可见性条件；角色过滤先于 100 条列表上限，避免高活跃租户漏掉当前用户真正可处理的待办。临期窗口允许 60–86400 秒。

运营历史通过无 Run 外键的 `approval_audit_facts` 独立保留，并由 `GET /api/v1/approvals/trend?days=30` 提供完整 UTC 日期序列。Console 支持 7/14/30/90 天新建、决定和超时趋势；即使已结束会话被删除，角色可见的审计事实仍可聚合。详见 `approval-retained-audit-trend-v1.zh-CN.md`。

## 6. 验证基线

- Control Plane Maven：56/56；包含 V1–V7 迁移、权限、数据库侧角色过滤、SLA/趋势聚合、Run 删除后事实保留、幂等、超时和取消。
- Execution unittest：90/90；真实 PostgreSQL + MinIO，包含 checkpoint 恢复、分支路由、进度单调和恢复任务契约。
- Console Vitest：28/28；生产构建通过。
- Endpoint catalog：236/236，100%，无重复、悬空或未登记路由。
- Stack smoke：真实完成 `RUNNING → WAITING_APPROVAL → PENDING → RUNNING → SUCCEEDED`，并验证审批幂等、SLA/独立保留趋势随生命周期变化、事件/节点审计和清理。

## 7. 已知限制与下一步

本功能的 v1 闭环已经完成，但不代表系统整体生产化完成。后续优先项：

1. 多实例超时扫描的 leader/分片治理和高并发审批压测；
2. 委派、转交、会签、撤回、评论/附件和通知渠道；
3. 跨 Worker 的可恢复并行分支与部分成功补偿；
4. 审批 SLA 已提供实时摘要和独立事实趋势；后续补外部 Alertmanager/Webhook、明确留存期限和长期不可变归档；
5. 浏览器 E2E 覆盖键盘、无障碍、过期与并发冲突路径。
