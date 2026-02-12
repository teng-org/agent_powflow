# api

FastAPI 接口层，对接知识库管理前端与运行时。

## 已实现接口（MVP+）

### 系统

- `GET /api/v1/system/health`
- `GET /api/v1/system/context`
- `GET /api/v1/admin/system/upload-settings`（平台管理员）
- `PUT /api/v1/admin/system/upload-settings`（平台管理员）

### 管理员鉴权

- `POST /api/v1/admin/auth/login`
- 登录返回带过期时间的签名 token（HMAC），包含 `role` 声明。

### 知识库管理（多库）

- `GET /api/v1/admin/kbs`
- `POST /api/v1/admin/kbs`
- `DELETE /api/v1/admin/kbs/{kb_id}`

### 文档与切片（异步）

- `POST /api/v1/admin/kbs/{kb_id}/documents/ingest`
- `POST /api/v1/admin/kbs/{kb_id}/documents/upload`（multipart，多文件）
- `GET /api/v1/admin/kbs/{kb_id}/documents`
- `GET /api/v1/admin/kbs/{kb_id}/documents/{doc_id}`
- `DELETE /api/v1/admin/kbs/{kb_id}/documents/{doc_id}`
- `POST /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/reindex`
- `GET /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks`
- `PUT /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft`
- `DELETE /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft`
- `POST /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/commit`

### 任务

- `GET /api/v1/admin/tasks`
- `GET /api/v1/admin/tasks/stream`（SSE）
- `GET /api/v1/admin/tasks/{task_id}/events`
- `POST /api/v1/admin/tasks/{task_id}/cancel`
- `GET /api/v1/tasks/{task_id}`

### 检索与 Agent

- `POST /api/v1/retrieval/search`
- `POST /api/v1/agent/run`

### API 池管理

- `GET /api/v1/admin/api-pool`
- `POST /api/v1/admin/api-pool`
- `PUT /api/v1/admin/api-pool/{endpoint_id}`
- `DELETE /api/v1/admin/api-pool/{endpoint_id}`
- `POST /api/v1/admin/api-pool/{endpoint_id}/set-default`
- `POST /api/v1/admin/api-pool/reload-default`

## 鉴权与上下文

- 管理员登录后使用 `Authorization: Bearer <access_token>` 调用后台接口。
- 角色分级：
  - `PLATFORM_ADMIN`：全部接口（含 API 池）
  - `KB_MANAGER`：知识库读写 + 任务管理
  - `AUDITOR`：知识库只读 + 任务只读
- 所有请求建议带系统上下文：
  - `X-Tenant-Id`
  - `X-Actor-Id`
  - `X-Trace-Id`
  - `X-Source-Service`

## 异步约定

- 文档入库与重建均为异步任务，接口返回 `task_id`。
- 前端通过 `GET /api/v1/tasks/{task_id}` 或 `GET /api/v1/admin/tasks` 跟踪状态。
- 删除文档时，后端会尝试取消该文档未完成任务并清理数据。
- 前端任务中心可优先使用 SSE 接口实时订阅任务变化。

### 常见文档上传支持

- `upload` 接口支持常见文档：`txt/md/html/pdf/docx`
- 后端会先解析文本，再进入异步切片/嵌入/索引流程
- 不支持格式会在返回的 `skipped` 列表给出原因
- 默认单文件上限 `20MB`，可通过系统配置 API 调整
