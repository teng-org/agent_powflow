# Control Plane Task 运营权威迁移 v1

- 日期：2026-08-04
- 状态：Phase 1 已实现并验证
- 关联：`control-plane-knowledge-authority-v1.zh-CN.md`

## 1. 本阶段边界

Control Plane 已接管以下 Task 运营命令与查询：

- 租户/知识库范围任务列表；
- 单任务状态与事件查询；
- 单任务取消；
- 批次父任务取消及活动子任务级联；
- 终态任务、事件、去重锁和空批次清理；
- 取消事实写入 `task_events`，并以 `state_owner=CONTROL` 标识已由 Control 接管的状态。

Execution Plane 新增仅供 Control 调用的 `POST /api/v1/internal/tasks/revoke`。Control 先在 PostgreSQL 事务内提交取消状态与事件，再请求 Execution 撤销 Celery 任务；即使 broker revoke 暂时不可用，Worker 仍会读取持久化取消标志并停止后续阶段。

## 2. 共享 schema 过渡

Flyway V10 创建或接管 `tasks`、`task_events`、`task_dedup_locks` 与 `task_batches`，并新增 `tasks.state_owner`。迁移同时覆盖：

- 空 schema；
- 已由 Execution 建表的共享 `public` schema；
- Control V1 逐步升级到 V10；
- Hibernate `ddl-auto=validate`。

本阶段不复制 Task 数据，Control 与 Execution 仍读取同一 PostgreSQL 事实表。

## 3. API

- `GET /api/v1/admin/tasks`
- `DELETE /api/v1/admin/tasks`
- `POST /api/v1/admin/tasks/{taskId}/cancel`
- `GET /api/v1/admin/tasks/{taskId}/events`
- `GET /api/v1/tasks/{taskId}`
- `POST /api/v1/internal/tasks/revoke`（Execution internal）

统一 Console 的任务中心、知识库任务列表、事件、取消和历史清理均已切换到 Control Plane。

## 4. 已验证证据

- Control Plane：81/81，真实 PostgreSQL，无失败、无跳过；
- Execution：128/128，无跳过（其中 API 集成 21/21，真实 PostgreSQL + TLS MinIO）；
- Console：48/48，production build 通过；
- Endpoint catalog：259/259，100%，无重复、悬空或未登记路由；
- 真实 HTTP smoke：临时 PENDING Task 经 Control 列表可见，取消后状态为 `CANCELLED`、`state_owner=CONTROL`、取消事件存在、Execution revoke 接收成功，测试数据严格清理。

## 5. 仍未完成的 Task Phase 2

Task 创建、进度更新和 Worker 终态回写仍由 Execution 直接操作共享表。下一阶段需：

1. Control 创建 Task 与不可变身份；
2. 使用事务 Outbox 投递 Execution；
3. Worker 仅通过 Control Internal Task Event API 回写状态、进度和事件；
4. 增加事件幂等键、租约或 attempt 约束；
5. 迁移 Task SSE 后关闭 Execution 的公开 Task 写接口。
