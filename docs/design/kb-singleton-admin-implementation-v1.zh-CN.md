# 知识库产品化实现方案 v2（多库 + 管理员专用）

- 版本：v2
- 日期：2026-02-09
- 范围：`execution-plane` + `kb-admin-web` + 调试文档

## 1. 目标与硬约束

1. 知识库不对租户用户开放，仅管理员维护。
2. 支持全局多库（每个租户可创建多个知识库），由管理员进行建库/删库/维护。
3. 对话检索按 `kb_id` 进行命名空间隔离。
4. 文档处理必须异步（Celery），避免 API 阻塞。
5. 文档删除需要取消未完成任务并清理向量/元数据。
6. Chunk 删除/编辑采用“预删除/预编辑 -> 提交生效”的两阶段机制。

## 2. 接口设计（Execution API）

### 2.1 管理员鉴权

- `POST /api/v1/admin/auth/login`
  - 输入：`username/password`
  - 输出：`access_token`（HMAC 签名 token，含 `role` 与过期时间）

### 2.2 知识库管理

- `GET /api/v1/admin/kbs`
- `POST /api/v1/admin/kbs`
- `DELETE /api/v1/admin/kbs/{kb_id}`
- `POST /api/v1/admin/kbs/{kb_id}/documents/ingest`
- `GET /api/v1/admin/kbs/{kb_id}/documents`
- `GET /api/v1/admin/kbs/{kb_id}/documents/{doc_id}`
- `GET /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks`
- `POST /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/reindex`
- `DELETE /api/v1/admin/kbs/{kb_id}/documents/{doc_id}`
- `PUT /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft`
- `DELETE /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft`
- `POST /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/commit`

### 2.3 任务查询

- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/admin/tasks`
- `GET /api/v1/admin/tasks/stream`（SSE）
- `GET /api/v1/admin/tasks/{task_id}/events`
- `POST /api/v1/admin/tasks/{task_id}/cancel`

### 2.4 对话检索/执行

- `POST /api/v1/retrieval/search`（显式传 `kb_id`）
- `POST /api/v1/agent/run`（显式传 `kb_id`）

## 3. 数据流

1. 管理员在 `kb-admin-web` 提交文档。
2. API 校验管理员 token 后，投递 `parse_queue`。
3. Worker 执行：`PARSING -> CHUNKING -> EMBEDDING -> INDEXING -> INDEXED`。
4. 元数据写入 Mongo（documents/chunks），向量写入 Qdrant。
5. 前端通过任务中心轮询/刷新状态；页面关闭不影响任务执行。
6. 删除文档时触发：任务取消请求 -> 任务 revoke -> 文档与向量清理。
7. Chunk 修改先写入 draft，只有提交时才真正改写 chunk 并重嵌入。

## 4. 存储模型

### 4.1 Mongo

- `knowledge_bases`
  - `tenant_id`, `kb_id`, `name`, `summary`, `stats`, `created_at`, `updated_at`
- `documents`
  - `tenant_id`, `kb_id`, `doc_id`, `filename`, `raw_text`, `status`, `created_at`, `updated_at`
- `chunks`
  - `tenant_id`, `kb_id`, `doc_id`, `chunk_id`, `chunk_index`, `text`, `embedding`, `metadata`, `updated_at`
- `tasks`
  - `task_id`, `tenant_id`, `kb_id`, `doc_id`, `task_type`, `status`, `stage`, `cancel_requested`, `error_message`
- `task_events`
  - `task_id`, `tenant_id`, `kb_id`, `doc_id`, `event_type`, `stage`, `payload`, `ts`
- `chunk_drafts`
  - `tenant_id`, `kb_id`, `doc_id`, `chunk_id`, `action(delete|edit)`, `text`, `updated_at`

### 4.2 Qdrant

- collection 命名：`{prefix}_{tenant_id}_{kb_id}`
- payload 包含：`tenant_id/kb_id/doc_id/chunk_id/chunk_index/text/source`

## 5. 前端实现（kb-admin-web 产品布局）

1. 左侧可折叠知识库栏：卡片展示库统计（文档数、大小、chunk数、词数、摘要）。
2. 中央文档卡片区：上传入口、状态可视化、重建/删除入口。
3. 右侧任务中心：任务悬浮追踪、取消入口。
4. 二级切片页：便签式 chunk 布局，支持预删除、预编辑、提交生效。
5. 提交前展示变更汇总确认（预删除数、预编辑重嵌入数、明细）。
5. 上传弹窗：支持文件拖拽和文本内容编辑，异步任务提交后即时反馈。

## 6. 安全与治理

1. KB 接口全部挂在 `/api/v1/admin/kbs/*`，统一 `Authorization: Bearer <token>`。
2. 统一系统上下文头：`X-Tenant-Id/X-Actor-Id/X-Trace-Id/X-Source-Service`。
3. 任务取消与删除逻辑保证幂等，避免孤儿向量/脏元数据。
4. 角色分级：`PLATFORM_ADMIN`、`KB_MANAGER`、`AUDITOR`。

## 7. 本阶段未完成项

1. `control-plane` 尚未接管管理员鉴权与 RBAC（当前由 execution-api 简化承接）。
2. 尚未落地正式审计表（当前通过 trace_id +日志追踪）。
3. 文件上传目前为文本直传，未接入对象存储文件流上传。

## 8. 下一步

1. 将 KB 元数据权威状态迁移至 `control-plane`（PostgreSQL）。
2. 将管理员登录从静态 token 升级为 JWT + 角色权限模型。
3. 补充文档格式解析器（PDF/DOCX）与异步预处理链路分层任务。
4. 任务中心升级为 SSE 推送，减少轮询压力。
