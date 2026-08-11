# api

FastAPI 接口层，对接知识库管理前端与运行时。

启动 API 前必须先执行 `python -m retrieval.schema_migrations`。项目 Docker CMD 已将迁移作为 Uvicorn 的前置门禁；数据库未迁移、迁移失败、版本过新或校验和漂移时 API 不会启动。

## 已实现接口（MVP+）

Control Plane 通过 `POST /api/v1/internal/control-runs/dispatch` 投递新运行或带 `approval_resume` 的审批恢复任务；恢复任务使用独立 task id，避免与首次投递冲突。

### 系统

- `GET /api/v1/system/health`
- `GET /api/v1/system/context`
- `GET /api/v1/admin/system/upload-settings`（平台管理员）
- `PUT /api/v1/admin/system/upload-settings`（平台管理员）

### 管理员鉴权

- `POST /api/v1/admin/auth/login`
- 登录返回带过期时间的签名 token（HMAC），包含 `role` 声明。

### 知识库管理（多库）

以下公开 KB 元数据变更接口属于兼容期遗留入口；统一 Console 已切换到 Control Plane，不应再新增调用方。Document、Chunk 与 Task 接口仍由 Execution Plane 承载，待后续权威迁移。

- `GET /api/v1/admin/kbs`
- `POST /api/v1/admin/kbs`
- `DELETE /api/v1/admin/kbs/{kb_id}`
- `DELETE /api/v1/internal/kbs/{tenant_id}/{kb_id}`（仅供 Control Plane 调用，使用 `X-Internal-Token`，负责跨 PostgreSQL/MinIO/Qdrant 的物理清理与修复义务登记）

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

统一 Console 的任务查询、事件、取消和终态历史清理已迁移到 Control Plane；以下公开接口属于兼容期入口。Execution 仍负责 Phase 2 尚未迁移的任务创建、流水线执行与 Worker 状态更新。

- `GET /api/v1/admin/tasks`
- `GET /api/v1/admin/tasks/stream`（SSE）
- `GET /api/v1/admin/tasks/{task_id}/events`
- `POST /api/v1/admin/tasks/{task_id}/cancel`
- `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/internal/tasks/revoke`（仅供 Control 调用；不写权威状态，只执行 Celery revoke）

任务状态、事件和取消均绑定请求租户。普通轮询接口在任务属于其他租户时返回 `UNKNOWN` 且不带记录；管理事件和取消接口返回 404。重复投递不得改变任务的既有身份或重新打开终态任务。

### 检索与 Agent

- `POST /api/v1/retrieval/search`
- `POST /api/v1/agent/run`
- `POST /api/v1/internal/control-runs/dispatch`：Control Plane 内部投递入口，使用 `X-Internal-Token`，任务进入带 DLX 的 Celery `control_run_queue`。

### API 池管理

- `GET /api/v1/admin/api-pool`
- `POST /api/v1/admin/api-pool`
- `PUT /api/v1/admin/api-pool/{endpoint_id}`
- `DELETE /api/v1/admin/api-pool/{endpoint_id}`
- `POST /api/v1/admin/api-pool/{endpoint_id}/set-default`
- `POST /api/v1/admin/api-pool/reload-default`

### API Token 用量统计（30 天）

- `GET /api/v1/admin/usage/api-tokens/summary`
- `GET /api/v1/admin/usage/api-tokens/events`
- `GET /api/v1/admin/usage/api-tokens/budgets`
- `PUT /api/v1/admin/usage/api-tokens/budgets`
- `DELETE /api/v1/admin/usage/api-tokens/budgets/{budget_id}`
- `POST /api/v1/admin/usage/api-tokens/budgets/evaluate`
- `GET /api/v1/admin/usage/api-tokens/alerts`
- `PATCH /api/v1/admin/usage/api-tokens/alerts/{alert_id}`
- 统计维度包含：
  - `kb_id`
  - `API`（`api_path`，如 `/embeddings`、`/chat/completions`）
  - `model`
  - `api_key_fingerprint`（API Key 指纹，非明文）
  - `task_category`（如 `doc_embedding`、`summary_llm`、`query_embedding`）
  - `day`（UTC 按天）
- 费用估算：
  - 每条请求记录 `estimated_cost_usd`
  - 优先读取 API 池端点价格（`input_per_1k` / `output_per_1k`）
  - 若端点未配置价格，则回退到 `runtime-config.json` 的 `api_model_pricing`
- 预算与告警：
  - 支持 `TENANT / KB / MODEL / TASK_CATEGORY` 四类 scope
  - 支持 `daily_token_limit` 与 `daily_cost_limit`
  - 超限触发告警并进入 `OPEN` 状态，可 `ACK/RESOLVED`
- 保留策略：仅保留最近 30 天数据（自动清理）。

### Workflow Secret Vault（平台管理员）

- `GET /api/v1/admin/workflow-secrets`
- `POST /api/v1/admin/workflow-secrets`
- `PUT /api/v1/admin/workflow-secrets/{secret_id}`（轮换值并递增版本）
- `POST /api/v1/admin/workflow-secrets/{secret_id}/disable`
- `DELETE /api/v1/admin/workflow-secrets/{secret_id}`（只允许删除已禁用项）

Secret 按 tenant 隔离并使用 `WORKFLOW_SECRET_MASTER_KEY` 加密。主密钥未配置或格式非法时，写入、轮换和运行时解析均 fail closed。接口只返回名称、状态、版本、指纹和审计元数据，不返回 Secret 明文或数据库密文；请求审计也不记录值。

Workflow 的权威 Run、事件、节点与 attempt 查询由 Control Plane 提供。`GET /api/v1/admin/runs/{run_id}/nodes` 的每个节点包含有序 `attempts`；Execution Worker 只回写最小化 `NODE_ATTEMPT_*` 事件，不在事件中保存输入状态、Provider 响应正文或 Secret。

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
- 默认单文件上限 `50MB`，可通过系统配置 API 调整
