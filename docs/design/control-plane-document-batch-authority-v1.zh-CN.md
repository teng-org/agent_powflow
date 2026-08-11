# Control Plane 文档批处理权威 v1

## 目标与结论

Phase 2D 将文档批处理从 Execution 兼容入口迁移到 Control Plane。Control 负责批次、父 Task、子 Task、权威状态、事务 Outbox、取消和失败重试；Execution 只负责精确文档预检、不可变身份复核、Celery 投递和文档/Qdrant/MinIO 副作用；Worker 继续通过内部事件接口回写 Control。

本阶段覆盖 `chunk`、`embed`、`summarize`、`move`、`tag`、`delete` 六类批量命令。Console 已切换到 Control 接口，Execution 原公开批处理路由仅作为待退役兼容面保留。

## 接口契约

### Control 外部接口

- `POST /api/v1/admin/kbs/{kbId}/documents/batch`：原子创建一个批次、一个父 Task、1–500 个子 Task 和对应 Outbox。
- `GET /api/v1/admin/tasks/batches/{batchId}`：返回批次聚合、父 Task 和全部子 Task。
- `POST /api/v1/admin/tasks/batches/{batchId}/retry-failed`：只提取失败子项，以原参数创建新批次；不重开原批次。

批量请求保留输入文档顺序并去重。任一文档不存在、目标 KB 不可写、参数非法或任一文档已有活动写命令时，整个请求在写事务前/事务内失败，不产生半批次。

### Execution 内部接口

- `POST /api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/describe`：最多精确读取 500 个文档的 `doc_id/filename/status/chunk_count`，返回缺失 ID，不返回正文，也不依赖文档列表分页。
- `POST /api/v1/internal/tasks/dispatch`：除 Task 固有身份外，精确校验 `batch_id`、`batch_name` 和 `batch_is_parent=false`；父 Task 永不下发 Worker。

## 持久化与事务边界

Control 的一个批次创建事务包含：

1. 按 `doc_id` 排序获取 PostgreSQL transaction advisory lock；
2. 检查全部文档的活动命令冲突；
3. 创建 `RUNNING/BATCH_RUNNING` 父 Task；
4. 创建 `task_batches` 聚合行；
5. 为每个文档创建 `PENDING/QUEUED` 子 Task、`TASK_CREATED` 事件和一条 Outbox；
6. 一次提交，随后由 Outbox publisher 进行 at-least-once 派发。

父 Task 没有 Outbox。子 Task payload 携带不可变的批次身份与命令参数。`chunk_count=0` 的分块子项使用 `DOC_CHUNK`，已有分块使用 `DOC_RECHUNK`；embed 使用 `DOC_REINDEX`。

Control Flyway V13 与 Execution 显式迁移 V9 均以追加方式增加：

- `task_batches.cancelled_items INTEGER NOT NULL DEFAULT 0`；
- `(tenant_id, parent_task_id)` 索引；
- Control 侧非负计数约束。

Execution V1 基线保持不可变。开发库曾因把新列误放入 V1 产生 checksum 漂移，本阶段已恢复 V1 原始校验和，新结构只由 V9 引入。

## 聚合状态机

每次子任务回调、取消、Outbox 死信或重放后，Control 在批次行悲观锁下重新统计全部子项：

| 子项状态 | 批次/父 Task 结果 |
| --- | --- |
| 任一 `PENDING/RUNNING/CANCELLING` | `RUNNING / BATCH_RUNNING` |
| 无活动项且任一 `FAILED` | `FAILED / BATCH_FAILED` |
| 无失败项且任一 `CANCELLED` | `CANCELLED / BATCH_CANCELLED` |
| 全部 `SUCCEEDED` | `SUCCEEDED / BATCH_SUCCEEDED` |

失败优先于取消，取消不会再被误报为成功。聚合事件键包含成功/失败/取消/运行计数，重复刷新幂等。原批次失败项重试会创建新批次，保留原批次作为不可变审计事实。

## 并发与跨存储修正

真实两文档批量移动首次暴露了 Qdrant 首次建 collection 的并发竞争：两个 Worker 同时创建、delete/upsert 同一目标 collection，其中一个收到 500，批次按设计聚合为失败。

修复后，所有 `replace_chunks` collection 写操作使用基于 PostgreSQL `pg_advisory_xact_lock(hashtextextended(...))` 的 collection 级跨进程锁；移动同时按名称排序锁定源和目标 collection，避免死锁，并在已持锁路径跳过嵌套加锁。并发集成测试证明两个文档最大 collection 写并发为 1，且两个文档向量均保留。

知识库删除同时补齐 `task_batches`、`task_dedup_locks` 和 `summary_segment_cache` 清理。Task 删除通过 V12 外键级联清除派发 Outbox；Qdrant collection 和每个 MinIO 对象的全部版本/删除标记均纳入真实清理验收。

## Console 行为

- 文档列表支持逐项/全选与选择保留；
- 根据操作显示分块参数、目标 KB、标签和 replace/add 模式；
- 删除要求确认；空选择、同源目标、空标签等在提交前禁用；
- Task 卡片展示父批次聚合计数、子任务明细与“重试失败项”；
- 选中父 Task 时通过 Control 读取批次详情，不再依赖 Execution 批次接口。

## 验证证据（2026-08-08）

- Control：105/105；其中真实 PostgreSQL Flyway V1→V13、baseline 0、Hibernate validate 专项 6/6。
- Execution：148/148；真实隔离 PostgreSQL 和 TLS MinIO 用例均通过。
- Console：52/52，生产构建通过。
- Debug endpoint catalog：279/279，100%，无重复、悬空或遗漏路由。
- Python `compileall`、Control/Execution/Worker/Console/debug-web 镜像构建与启动通过。
- 真实 smoke：2 个入库 Task，4 个批次（分块、标签、移动、删除），8 个批次子 Task，102 条 Execution 回调。
- smoke 断言：父子聚合计数、Outbox `PUBLISHED`、replace 标签、`chunk_overlap=0`、移动与删除全部通过；PostgreSQL 业务行、Qdrant collection、MinIO 当前对象及全部历史版本均为 0。

可重复命令：

```powershell
.\tools\smoke\document-batch-authority-smoke.ps1
node .\tools\audit\verify-endpoint-catalog.mjs --json
```

## 尚未关闭的边界

- ~~`UPLOAD_ONLY` / `UPLOAD_AND_CLEAN` 非索引上传仍需持久化意图、超时恢复与对象生命周期闭环；~~ 已由 Phase 2E 完成，见 `control-plane-non-index-upload-authority-v1.zh-CN.md`；
- Execution 公开批处理/单文档写入口仍需遥测、冻结和退役，防止双权威；
- 旧 `apps/kb-admin-web` 仍与统一 Console 重叠，需按 ADR-011 完成页面退役；
- move 的不可恢复外部 Qdrant 故障仍沿用现有跨存储修复边界，后续应增加面向移动的持久修复操作与故障注入；
- summary 仍依赖外部 Provider 稳定性，本阶段确定性 smoke 不把它作为发布门禁。
