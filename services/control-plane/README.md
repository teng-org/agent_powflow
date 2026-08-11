# Control Plane

## Task Authority Phase 2A / 2B / 2C / 2D / 2E / 2F

Control Plane now owns text ingest, all multipart upload modes, single-document commands, document-batch parent/child Tasks, aggregate state, event state, and durable dispatch. Multipart uses a two-phase upload intent so binary data never enters the outbox. Non-index modes finish as `DOC_UPLOAD / SUCCEEDED` with a `DOC_UPLOAD_ARTIFACT / COMPLETED` durable fact; indexed uploads activate dispatch only after staging. Flyway V11 adds event idempotency and `task_dispatch_outbox`; V12 binds each outbox row to its Task with `ON DELETE CASCADE`; V13 adds authoritative batch cancellation counts and parent lookup.

- `POST /api/v1/admin/kbs/{kbId}/documents/ingest`
- `POST /api/v1/admin/kbs/{kbId}/documents/upload`
- `POST /api/v1/admin/kbs/{kbId}/documents/batch`
- `GET /api/v1/admin/tasks/batches/{batchId}`
- `POST /api/v1/admin/tasks/batches/{batchId}/retry-failed`
- `PATCH /api/v1/admin/kbs/{kbId}/documents/{docId}/freeze`
- `PATCH /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/freeze`
- `PUT /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/draft`
- `DELETE /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/draft`
- `GET /api/v1/admin/task-dispatches[?status=DEAD_LETTER]`
- `POST /api/v1/admin/task-dispatches/{outboxId}/replay`
- `POST /api/v1/internal/tasks/{taskId}/events`

Phase 2C creates durable commands for KB/document summary, chunk/reindex, chunk draft commit, move, tag, and delete. Phase 2D adds atomic batches, aggregate state, cancellation-safe terminal semantics, and failed-item retry. Phase 2E closes the non-index upload crash window with durable intent, completion and cleanup facts. Phase 2F makes Control the only public KB write plane by proxying document/chunk freeze and chunk drafts to tenant-scoped Execution internal APIs. See the matching Phase 2 design documents under `docs/design/`.

## Run

```bash
mvn spring-boot:run
```

## Storage Baseline

- Authoritative business data: PostgreSQL
- Optional event/big-payload store: MongoDB (execution/event side)
- Schema owner: Flyway `V1`–`V13`; Hibernate uses `ddl-auto=validate` and never mutates schema. V9 establishes Control ownership of KB metadata and tenant bindings; V10 adds Task operational authority; V11/V12 add reliable Task dispatch, event idempotency, and outbox lifecycle integrity; V13 completes batch aggregate counters.
- Non-empty legacy/shared schemas without Flyway history are baselined at version `0`, then all migrations run.

Run PostgreSQL migration integration tests explicitly:

```powershell
$env:CONTROL_TEST_POSTGRES_JDBC_URL = 'jdbc:postgresql://localhost:5432/agentpowflow'
$env:CONTROL_TEST_POSTGRES_USER = 'apf'
$env:CONTROL_TEST_POSTGRES_PASSWORD = 'apf'
mvn --batch-mode test
```

Each integration test uses and removes an isolated `apf_cp_it_<32 hex>` schema.

## Endpoints

- `GET /api/v1/system/health`
- `GET /api/v1/system/context`
- `GET /api/v1/approvals[?status=PENDING]`
- `GET /api/v1/approvals/stats[?due_within_seconds=900]`
- `GET /api/v1/approvals/trend[?days=30]`
- `GET /api/v1/approvals/{approvalId}`
- `POST /api/v1/approvals/{approvalId}/decision`
- `GET /api/v1/admin/approval-alerts[?status=OPEN]`
- `GET /api/v1/admin/approval-alerts/stats`
- `GET /api/v1/admin/approval-alerts/config`
- `POST /api/v1/admin/approval-alerts/evaluate`
- `POST /api/v1/admin/approval-alerts/{alertId}/acknowledge`
- `POST /api/v1/admin/approval-alerts/{alertId}/replay`
- `POST /api/v1/internal/runs/{runId}/approvals/suspend`（internal token + active lease）

### 知识库元数据与租户绑定

- `GET /api/v1/admin/kbs`
- `POST /api/v1/admin/kbs`
- `PATCH /api/v1/admin/kbs/{kbId}`
- `PATCH /api/v1/admin/kbs/{kbId}/visibility`
- `PATCH /api/v1/admin/kbs/{kbId}/freeze`
- `DELETE /api/v1/admin/kbs/{kbId}`
- `GET /api/v1/admin/kbs/{kbId}/bindings`
- `PUT /api/v1/admin/kbs/{kbId}/bindings/{targetTenantId}`
- `DELETE /api/v1/admin/kbs/{kbId}/bindings/{targetTenantId}`

KB 元数据和绑定写入以 Control Plane 为权威入口；文本与 multipart 索引入库、单文档写命令和批处理父子 Task 均由 Control 创建并可靠派发，文档/切片副作用仍由 Execution 执行。删除 KB 时 Control 先校验租户归属，再通过带超时和内部凭据的 Execution 接口完成 PostgreSQL、MinIO、Qdrant 物理清理。

### Task 权威（Phase 1 + Phase 2A / 2B / 2C / 2D / 2E）

- `GET /api/v1/admin/tasks`
- `DELETE /api/v1/admin/tasks`
- `POST /api/v1/admin/tasks/{taskId}/cancel`
- `GET /api/v1/admin/tasks/{taskId}/events`
- `GET /api/v1/tasks/{taskId}`

Control 负责查询、租户隔离、取消、批次级联和历史清理，并已接管文本、三种 multipart 模式、单文档写命令及批处理编排的任务创建、两阶段暂存、事务 Outbox、非索引完成/清理事实、父子聚合与 Worker 状态回写。

审批决定使用 revision 乐观锁；相同决定重复提交幂等。公开 DTO 不返回执行 checkpoint，完整契约见 `docs/design/durable-human-approval-runtime-v1.zh-CN.md`。

审批实时队列摘要使用 `GET /api/v1/approvals/stats`；独立保留的 1–90 天 UTC 趋势使用 `GET /api/v1/approvals/trend?days=30`。趋势沿用 tenant/role 数据库侧可见性过滤，完整设计见 `docs/design/approval-retained-audit-trend-v1.zh-CN.md`。

审批临期、逾期和自动超时会先持久化为独立告警事实，再由可重试 Outbox 投递到部署配置的 Webhook。URL 不进入数据库；默认未配置时安全保持 `DISABLED`。完整契约与环境变量见 `docs/design/approval-alert-delivery-outbox-v1.zh-CN.md`。
