# 以下是AI产品规划师给出的内容：

下面给你一份“**超详细系统介绍 + 任务规划**”，你可以直接放进 `docs/requirements/product-vision.md` 和 `docs/design/roadmap.md` 里当作项目总纲。内容是按你们前面确定的：**Vue + Spring Boot（控制平面）+ Python（执行平面：FastAPI/Celery）+ Mongo（业务元数据）+ 向量库 + 对象存储**，并且支持 **Agent Canvas（DSL 为真相，画布只是视图）**、模块插拔、多租户隔离、状态同步与可观测。

（我会把关键工程实践与“生产级编排/治理”的要点也写进去，参考了近期对 agent 编排与治理的工程化建议、特性开关最佳实践、ADR 记录法等资料。([HatchWorks AI][1])）

---

# 1. 系统定位与价值主张

## 1.1 一句话定位

一个可私有化部署的“**个人/团队知识库 + Agent 平台**”，提供：

* 文档接入、解析、切分、向量化、检索（RAG）
* 可编排 Agent（Workflow DSL + Canvas 管理）
* 多租户/多用户、权限、审计、配额、成本归因
* 可售卖的“版本/能力包/行业包”，减少长期定制维护成本

## 1.2 设计哲学（决定你们能不能“卖版本”）

* **Definition-first**：运行时只认 DSL/配置版本，UI 只是投影（Canvas 便于管理迁移）
* **Control Plane / Execution Plane 分离**：Spring 负责“管”，Python 负责“跑”
* **Run/Task 为统一审计单位**：可追踪、可回放、可计费
* **可治理优先**：权限、配额、审计、灰度开关是企业可用的前提 ([HatchWorks AI][1])
* **ADRs 固化关键决策**：避免团队遗忘上下文、减少返工 ([Amazon Web Services, Inc.][2])

---

# 2. 总体分层架构（从“层”到“实例”）

## 2.1 层级（Layered Architecture）

1. **Presentation 层**：`apps/web-app`（用户端）、`apps/web-admin`（管理端）
2. **Gateway/Edge 层（可选）**：Nginx / API Gateway（限流、TLS、路由、CORS）
3. **Control Plane（Spring Boot）**：用户/租户/RBAC、资源与配置、任务与运行权威状态、审计、配额、包管理
4. **Execution Plane（Python）**：

   * FastAPI：执行平面入口（可选；也可让 worker 直接消费队列）
   * Celery Workers：按队列拆分（parse/embed/index/agent）
   * Runtime：Workflow 编译与执行引擎、Tool 执行器、Retriever/Rerank 适配
5. **Data Plane（存储）**：

   * MongoDB：权威元数据（User/Tenant/Task/Run/Resource/Config/Package）
   * Object Storage（MinIO/S3）：原始文件、解析产物、chunk 文本（建议大文本不放 Mongo）
   * Vector DB（pgvector/Qdrant/Milvus）：向量检索
6. **Observability（可观测）**：日志、指标、追踪、审计报表（后期可接 Prometheus/Grafana/ELK）

## 2.2 实例拓扑（Deployment / Instances）

* `web-app`：静态资源（CDN/NGINX）
* `web-admin`：静态资源
* `control-plane`（Spring）：**无状态**多实例（2+）
* `execution-api`（FastAPI，可选）：无状态多实例（1+）
* `celery-worker-parse`：CPU 池（N）
* `celery-worker-embed`：GPU/CPU 池（N）
* `celery-worker-agent`：CPU+IO 池（N）
* `broker`：RabbitMQ/Redis（建议 RabbitMQ 更“企业稳”）
* `mongodb`：副本集（生产）
* `object-storage`：MinIO（开发/私有化）
* `vector-db`：单机/集群（按规模）

---

# 3. 关键业务对象与数据流（你们后面画图就按这个拆）

## 3.1 全局 ID（防串台的“硬规矩”）

每条链路都必须携带并落库：

* `tenant_id`, `user_id`
* `kb_id`, `doc_id`, `chunk_id`, `index_id`
* `task_id`（异步任务）
* `conversation_id`, `run_id`（对话/agent 执行）
* `config_version_id`（配置版本快照）
* `trace_id`（链路追踪）

## 3.2 两条必须先跑通的端到端链路

### A) 上传文档 → 入库索引（异步管线）

1. 前端上传 → Control Plane：创建 `Document` + `Asset(raw)`（文件落对象存储）
2. Control Plane 创建 `Task(type=DOC_INGEST)`，写 Mongo（权威状态）
3. 投递消息到 `parse_queue`
4. Worker 解析 → 产生 `Asset(parsed)` → 上报 `TaskEvent(progress/stage/log)`
5. Worker 切分 → 产生 `Chunk`（元数据入 Mongo，正文建议对象存储）
6. Worker embedding+upsert → VectorDB
7. Task 完成：`SUCCEEDED`，Document.status=`INDEXED`
8. 前端通过 SSE/WebSocket 看进度与日志

### B) 提问 → Agent 回答（带证据）

1. 前端发消息 → Control Plane：创建 `Message(user)`、创建 `Run(status=RUNNING)`
2. 投递 `agent_queue`（或调用 execution-api）
3. Agent Runtime：

   * 取对话上下文（限长）
   * 调用 Retriever（向量/混合检索）→ Evidence 列表
   * 可选 rerank
   * 可选 tool calls（记录 ToolCall）
   * LLM 生成答案（强制引用 evidence）
4. Run 完成：写 `Message(assistant)`、Run=`SUCCEEDED`，记录 tokens/cost

> 生产上强调“编排/观测/治理”，这些是 agent 平台可落地的关键点。([HatchWorks AI][1])

---

# 4. 数据库结构（MVP 级“权威集合/表”）

> 你们用 Mongo 的话，建议以 **Collection** 来组织；下面是“必须有”的最小集合（字段可逐步补齐）。

## 4.1 身份与权限

* `tenants`：`tenant_id`, `name`, `plan`, `status`, `created_at`
* `users`：`user_id`, `tenant_id`, `email`, `password_hash`, `status`, `created_at`
* `roles`：`role_id`, `tenant_id`, `name`
* `permissions`：`perm_id`, `name`（系统级）
* `user_roles`：`user_id`, `role_id`
* `role_perms`：`role_id`, `perm_id`

## 4.2 资源域

* `knowledge_bases`：`kb_id`, `tenant_id`, `name`, `settings_ref`
* `documents`：`doc_id`, `tenant_id`, `kb_id`, `object_key`, `hash`, `status`, `last_task_id`
* `chunks`：`chunk_id`, `tenant_id`, `kb_id`, `doc_id`, `chunk_index`, `metadata`, `text_ref`, `embedding_status`
* `indexes`：`index_id`, `tenant_id`, `kb_id`, `provider`, `namespace`, `embedding_model`, `dim`, `status`

## 4.3 任务与运行（核心）

* `tasks`：`task_id`, `tenant_id`, `type`, `status`, `stage`, `progress`, `input`, `result`, `error`, `attempt`, `heartbeat_at`
* `task_events`（可选落库）：`task_id`, `ts`, `type`, `payload`（也可只存最近 N 条）
* `conversations`：`conversation_id`, `tenant_id`, `created_by`
* `messages`：`message_id`, `conversation_id`, `role`, `content`, `related_run_id`
* `runs`：`run_id`, `tenant_id`, `conversation_id`, `status`, `agent_id`, `config_version_id`, `metrics(tokens,cost)`
* `tool_calls`：`tool_call_id`, `run_id`, `tool_id`, `input`, `output`, `status`

## 4.4 配置/包/治理

* `config_versions`：`config_id`, `scope(TENANT/KB/AGENT)`, `scope_id`, `version`, `data`, `active`
* `model_registry`：模型资源定义
* `tool_registry`：工具资源定义
* `feature_flags`：能力开关（按租户/百分比）——建议遵循“运行时控制、短生命周期、服务端评估”等原则 ([Unleash 文档][3])
* `license`：授权与 entitlements
* `audit_logs`：关键操作与调用轨迹（企业评估常见必需项）([Iterators][4])
* `packages`：可安装包元数据（签名、版本、依赖、权限）
* `package_installs`：租户安装记录

---

# 5. 任务规划（超详细、可直接开工）

下面按你说的“用 Codex 复制代码撰写+模块测试”的方式来拆：每个模块都能独立生成/跑通/写测试，再组合。

## Phase 0：项目骨架与工程规范（1 次性）

**目标**：仓库结构、开发环境、最小可运行服务骨架

* 建目录：`apps/ services/ packages/ infra/ docs/ .github/`
* `docker-compose.dev.yml`：Mongo + Broker + MinIO + VectorDB（先选一个）
* 统一 env：`.env.example`，本地用 `.env`（不入库）
* 代码规范：lint/format（前端 ESLint、后端 Checkstyle/Spotless、Python ruff/black）
* ADR 模板：`docs/decisions/ADR-000-template.md`（以后每个关键选型写 ADR）([Amazon Web Services, Inc.][2])

**验收**：

* `docker compose up` 能拉起基础依赖
* 3 个服务目录都有 README（如何启动）

---

## Phase 1：Control Plane（Spring）最小闭环（先让“管”跑起来）

### Epic 1.1 鉴权与租户

* 用户注册/登录（JWT + refresh）
* Tenant 初始化（个人仓库先单 tenant 也行，但字段要预留）
* RBAC：role/permission 最小实现
* Middleware：所有 API 统一注入 `tenant_id`, `user_id`

**验收**：登录后能拿到 token，访问 `/me` 返回用户+租户信息

### Epic 1.2 资源域 API（KB/Doc）

* CRUD：KB、Document（只存元数据）
* 上传接口：返回 `object_key`，文件落 MinIO
* 文档列表支持按 kb/状态过滤

**验收**：上传一份 PDF，Mongo 有 document 记录，MinIO 有对象

### Epic 1.3 Task 框架（权威状态 + 事件流）

* `POST /tasks` 创建任务（写 Mongo）
* `GET /tasks/{id}` 查询状态
* `POST /tasks/{id}/cancel` 软取消标记
* `POST /internal/task-events`（服务间上报）
* SSE：`GET /tasks/{id}/events`（前端实时看进度）

**验收**：你手动用脚本上报 progress，前端能看到更新

---

## Phase 2：Execution Plane（Python）任务工人（先把“跑”跑起来）

### Epic 2.1 基础执行框架

* Celery app + 队列划分：`parse_queue/embed_queue/index_queue/agent_queue`
* Worker 心跳与重试策略
* 统一事件上报 client：`report_event(task_id, stage, progress, log, result)`

**验收**：跑一个 `sleep_task`，Spring 端状态全程更新

### Epic 2.2 文档管线（Parse → Chunk → Embed → Upsert）

* parse：PDF → text（先简单；复杂 OCR 后面加）
* chunk：按长度 + overlap（保留 page metadata）
* embed：接入 embedding provider（可先用占位/开源模型）
* upsert：写向量库（chunk_id 作为主键，保证幂等）

**验收**：上传文档 → 触发 ingest task → 最终 Document=INDEXED，向量库可检索

> 这里建议坚持“可观测、可回放、失败可定位”的编排模式。([HatchWorks AI][1])

---

## Phase 3：Retrieval 层（RAG Core）

### Epic 3.1 Retriever API

* `POST /retrieval/search`：输入 query + kb_id + filters → 输出 evidence list
* 支持过滤：tenant_id/kb_id/doc_id/tags/time
* 最小策略：vector-only
* 可选：BM25/hybrid（后续）

**验收**：给定 query 返回 topK chunks，并带 doc/page 信息可引用

### Epic 3.2 Evidence 结构化

* 统一 Evidence schema（chunk_id、source、score、snippet）
* 在 Run 中保存 “本次检索快照”（可复现）

---

## Phase 4：Agent 层（Conversation ≠ Run）

### Epic 4.1 会话与消息

* Conversation CRUD
* Message append（用户消息）
* Run 创建/状态更新（RUNNING→SUCCEEDED/FAILED）

### Epic 4.2 Agent Runtime（先做“固定策略”）

* input：user message + conversation context（摘要/截断）
* retrieval：调用 Phase3
* generate：LLM 生成（先单模型）
* output：写 assistant message + citations

**验收**：一次提问能返回答案 + 引用来源（doc/page）

---

## Phase 5：Agent Canvas（DSL 为真相，UI 为投影）

### Epic 5.1 Workflow DSL v0（只支持 DAG）

* node types（先 8 个够用）：`input/retrieval/rerank/tool/llm/router/guard/output`
* edges + map（字段映射）
* schema 校验（输入输出、缺边、循环检测）
* compiler：dsl → execution plan（写入 Run 快照）

### Epic 5.2 Canvas（管理视图）

* 画布只存 layout：节点坐标/分组/备注
* 导入导出：DSL + 可选 layout
* 模板库：从 `packages/agent-templates` 读取

**验收**：不打开 UI 也能用 DSL 触发运行；打开 UI 只是更好编辑

---

## Phase 6：包化与“只允许官方功能/升级”

### Epic 6.1 Package Format + 签名

* `manifest.json`（name/version/depends/permissions/hash）
* 签名验证：未签名/签名不对 → 拒绝安装
* 租户安装记录、启用/禁用

### Epic 6.2 License + Entitlements

* license 文件/JWT：绑定 entitlements（capability、额度）
* 在 Run/Task 开始前校验（治理前置）

> Feature flag/能力开关建议坚持服务端评估、运行时控制、可灰度等原则。([Unleash 文档][3])

---

# 6. 你用 Codex 的“模块化开发与测试打法”（建议你就按这套跑）

## 6.1 每个 Epic 的交付物（你让 Codex 生成代码时就按此拆）

* API 定义（OpenAPI/接口文档）
* 数据模型（Mongo schema / DTO）
* 最小实现（happy path）
* 单元测试（核心函数/校验/编译器）
* 集成测试（docker compose 起依赖，跑一条链路）
* 观测：日志字段统一（tenant_id/task_id/run_id/trace_id）

## 6.2 “定义完成”的验收口径（Definition of Done）

* 代码可跑（dev compose）
* 关键接口有测试
* 失败路径能返回可读 error code
* 事件/状态可在前端看见
* 写 ADR（凡是选型/大改动）

---