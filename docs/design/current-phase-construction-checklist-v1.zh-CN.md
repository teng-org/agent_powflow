# 当前阶段施工清单 v1（MVP / 企业优先）

- 版本：v1
- 日期：2026-02-09
- 阶段：MVP（Phase A）
- 依据：
  - `docs/decisions/ADR-001-product-scope-v1.zh-CN.md`
  - `docs/decisions/ADR-002-domain-boundary-and-canvas-execution.zh-CN.md`
  - `docs/requirements/product-scope-clarification-v1.zh-CN.md`

## 1. 施工目标（本阶段交付）

1. 跑通两条主链路：
- 文档入库链路：上传 -> 解析 -> 切分 -> 向量化 -> 索引完成
- 对话链路：提问 -> 工作流执行 -> 检索/工具/生成 -> 返回答案与引用

2. 完成三类后台最小可用：
- 系统管理后台
- AI 能力配置后台
- 资源设施后台（模块库/知识库/工具库）

3. 建立 DSL 执行闭环：
- Canvas 编辑 -> DSL 发布 -> Python Runtime 执行 -> 状态回传

## 2. 系统响应流（System Response Flow）

## 2.1 用户对话响应流

1. `web-app` 提交消息到 Control Plane：`POST /api/v1/conversations/{id}/messages`
2. Control Plane 创建：
- `message(role=user)`
- `run(status=PENDING -> RUNNING)`
3. Control Plane 投递 `agent_queue`（RabbitMQ）
4. Execution Plane 消费任务，按 DSL 执行：
- 输入预处理
- 检索（可选 rerank）
- 工具调用（可选）
- LLM 生成
5. Execution Plane 回传事件：`POST /api/v1/internal/runs/{run_id}/events`
6. Control Plane 更新 run/message，并通过 SSE 推送前端：
- `GET /api/v1/runs/{run_id}/events`
7. 前端展示答案、引用、阶段进度、失败原因

## 2.2 文档入库响应流

1. `web-admin/resource-admin` 上传文件：`POST /api/v1/kbs/{kb_id}/documents`
2. Control Plane 写入 `documents` + 对象存储 `object_key`
3. 创建 `task(type=DOC_INGEST)`，投递 `parse_queue`
4. Worker 顺序执行 parse/chunk/embed/index
5. 每阶段回传 task event；Control Plane 写权威状态并 SSE 推送
6. 完成后：
- `documents.status = INDEXED`
- `tasks.status = SUCCEEDED`

## 2.3 Task / Run 语义约定（不可混用）

- Task：系统级异步工作单元（无 UI 语义）
- Run：用户可感知的 Agent 执行单元（有业务语义）
- 文档入库归类为 Task
- 对话执行归类为 Run
- Run 内部允许触发 Task（例如工具调用、索引更新等后台工作）

## 3. 数据流（Data Flow）

## 3.1 关键数据对象流转

- 前台输入：`input_payload`（text/image/audio/file refs）
- 编排定义：`workflow_definition`（DSL）+ `workflow_layout`（仅 UI）
- 执行快照：`run_snapshot`（dsl_version + provider versions + params）
- 过程事件：`run_events` / `task_events`
- 输出结果：`assistant_message + citations + metrics`

## 3.2 跨层数据边界

- Control Plane 持久化权威状态（PostgreSQL）
- Execution Plane 不直接改核心业务表，只通过内部事件 API 回传
- 大文本/原始文件走 Object Storage，不塞 Mongo 大字段
- 向量仅存于 Qdrant，业务元数据由 PostgreSQL/Mongo 分层维护

## 3.3 系统级上下文约定（System Context Contract）

所有跨服务调用、事件、日志必须携带以下上下文字段：

- `tenant_id`
- `actor_id` (`user_id` 或 `system`)
- `trace_id`
- `source_service`

执行约束：

- Spring Control Plane 负责注入与校验
- Python Execution Plane 只透传，不篡改
- 缺失上下文字段的请求或事件默认拒绝处理

## 3.4 执行前治理前置校验（不可绕过）

硬规则：

- 所有 Run / Task 在进入 Execution Plane 前，必须在 Control Plane 完成：
- `license entitlement` 校验
- `feature flag` 决策
- 未通过校验不得投递到执行队列

## 4. 实例模型（Deployment Instance Model）

## 4.1 开发/测试最小实例

- `web-app`: 1
- `web-admin`（含 AI/Resource 页面）: 1
- `control-plane` (Spring Boot): 1
- `execution-api` (FastAPI): 1（可选）
- `worker-parse`: 1
- `worker-embed`: 1
- `worker-agent`: 1
- `rabbitmq`: 1
- `postgres`: 1（dev）/ 主从或托管 HA（prod）
- `mongodb`: 1（dev）/ 副本集（prod）
- `qdrant`: 1
- `minio`: 1

## 4.2 生产初始建议（SME 规模）

- `control-plane`: 2 实例（无状态）
- `execution-api`: 2 实例（可选）
- `worker-parse/embed/agent`: 各 2 起步，按队列积压扩容
- `rabbitmq`: 持久化队列，开启死信交换器
- `postgres`: 主从 + 备份（PITR）
- `mongodb`: 副本集（事件/大字段）
- `qdrant`: 单集群（按数据量扩容）

## 5. 数据持久化分层与字段（PostgreSQL + Mongo + Object Storage + Qdrant）

说明：核心对象默认字段：
- `id`, `tenant_id`, `created_at`, `updated_at`, `created_by`, `updated_by`

## 5.1 PostgreSQL（权威 + 可运营）

### IAM 与租户治理表

### `tenants`
- `id` (uuid, pk)
- `tenant_code` (unique)
- `name`
- `status` (`ACTIVE|SUSPENDED`)
- `plan`
- `limits` (`max_users`, `max_agents`, `max_storage_mb`)

### `users`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `email` (unique within tenant, index)
- `password_hash`
- `status`
- `last_login_at`

### `roles`
- `id`, `tenant_id`, `name`, `description`

### `permissions`
- `id`, `code` (unique), `name`

### `user_roles`
- `id`, `tenant_id`, `user_id`, `role_id`

### `role_permissions`
- `id`, `role_id`, `permission_id`

### 工作流与版本表

### `workflows`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `name`
- `status` (`DRAFT|PUBLISHED|ARCHIVED`)
- `latest_version_no`

### `workflow_versions`
- `id` (uuid, pk)
- `workflow_id` (fk, index)
- `tenant_id` (fk, index)
- `version_no`
- `dsl` (jsonb)
- `layout` (jsonb)
- `checksum`
- `published_at`

### 会话与执行表

### `conversations`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `title`
- `created_by`
- `status`

### `messages`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `conversation_id` (fk, index)
- `role` (`user|assistant|system`)
- `content` (text / jsonb)
- `attachments` (jsonb)
- `related_run_id` (fk, nullable)

### `runs`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `conversation_id` (fk, index)
- `source` (`user|system|tool|scheduler`)
- `workflow_id`, `workflow_version_id`
- `status` (`PENDING|RUNNING|SUCCEEDED|FAILED|CANCELLED`)
- `input_payload` (jsonb)
- `output_payload` (jsonb)
- `citations` (jsonb)
- `metrics` (jsonb: tokens/cost/latency)
- `started_at`, `finished_at`
- `error_code`, `error_message`

### `tasks`
- `id` (uuid, pk)
- `tenant_id` (fk, index)
- `type` (index)
- `source` (`user|system|tool|scheduler`)
- `status`, `stage`, `progress`
- `input` (jsonb), `result` (jsonb)
- `attempt`, `heartbeat_at`
- `error_code`, `error_message`

### 运营治理表

### `audit_logs`
- `id`, `tenant_id`, `actor_id`, `action`
- `resource_type`, `resource_id`
- `result`, `trace_id`, `detail` (jsonb)

### `quota_counters`
- `id`, `tenant_id`, `quota_key`, `period`, `used_value`, `limit_value`

### `billing_records`
- `id`, `tenant_id`, `run_id`/`task_id`
- `metric_type`, `amount`, `unit_price`, `total_cost`, `recorded_at`

## 5.2 MongoDB（事件流 / 大字段 / 快速演化）

### `run_events`（建议 TTL/归档）
- `event_id`, `tenant_id`, `run_id` (index)
- `stage`, `progress`, `event_type`, `payload`, `ts`

### `task_events`（建议 TTL/归档）
- `event_id`, `tenant_id`, `task_id` (index)
- `stage`, `progress`, `log`, `payload`, `ts`

### `tool_call_payloads`（大输入输出可拆分）
- `id`, `tenant_id`, `run_id`, `tool_id`
- `input_raw`, `output_raw`, `size_bytes`, `ts`

## 5.3 Object Storage（MinIO/S3）

- 原始上传文件
- 解析后文本
- chunk 正文大字段（可按策略冷热分层）

## 5.4 Qdrant（向量检索）

- collection 维度：按 `tenant_id + kb_id` 命名空间
- point payload 至少包含：
- `tenant_id`, `kb_id`, `doc_id`, `chunk_id`, `chunk_index`, `source`, `text_ref`
- metadata 仍保留在 PostgreSQL/Mongo 中做运营查询

## 6. 技术选型（当前阶段固定）

## 6.1 后端与运行

- Control Plane: `Spring Boot 3.x` + `Spring Security` + `Spring Data JPA` + `PostgreSQL`
- Execution Plane: `Python 3.11+` + `FastAPI` + `Celery`
- Broker: `RabbitMQ`
- 权威数据库: `PostgreSQL`
- 事件/大字段数据库: `MongoDB`
- 向量库: `Qdrant`
- 对象存储: `MinIO`（S3 API）

## 6.2 前端

- `Vue 3` + `TypeScript`
- 状态管理：`Pinia`
- 网络层：`Axios`（或 `fetch` 封装）
- 实时事件：`SSE` 优先，WebSocket 预留

## 6.3 可观测与运维

- 日志：结构化 JSON
- 指标：Prometheus（后续）+ 自定义业务指标
- 链路追踪：OpenTelemetry（建议）
- 部署：Docker Compose（当前），K8s（后续）

## 7. 施工任务清单（可执行）

## 7.1 Control Plane（Spring）

- [ ] 建立项目骨架与模块分包（iam/knowledge/orchestration/agent/governance）
- [ ] JWT + RBAC + tenant context 注入
- [ ] KB/Document API + MinIO 上传
- [ ] Task/Run 权威状态 API
- [ ] Internal Event API（仅执行层可调用）
- [ ] SSE 事件流接口
- [ ] PostgreSQL schema + migration（Flyway/Liquibase）

## 7.2 Execution Plane（Python）

- [ ] Celery app 初始化（parse/embed/agent 队列）
- [ ] Worker 幂等框架（任务去重键）
- [ ] 文档流水线 worker（parse/chunk/embed/index）
- [ ] Agent runtime（加载 workflow_version 执行）
- [ ] Internal Event Client（回传 run/task 事件）
- [ ] 重试 + 超时 + DLQ 策略
- [ ] 固定失败策略落地（v1 不可配置）：
- [ ] 节点失败默认立即 `FAIL_RUN`，不允许部分结果提交（`output` 节点除外）
- [ ] Tool 节点失败默认重试 2 次（指数退避），失败后按“无 fallback”处理并失败运行
- [ ] 引用不足默认不阻断生成，但响应需标记 `low_evidence=true`

## 7.3 前端（Web/App + Admin）

- [ ] 对话页：输入、多模态开关、答案与引用展示
- [ ] 管理后台：用户/租户/角色、运行统计与审计
- [ ] AI 配置后台：能力开关、模型和检索策略
- [ ] 资源后台：模块库/知识库/工具库管理页
- [ ] Canvas 页面：DAG 编辑、发布、版本切换
- [ ] SSE 状态面板：run/task 时间线

## 7.4 数据与基础设施

- [ ] docker compose 拉起 Mongo/RabbitMQ/Qdrant/MinIO
- [ ] docker compose 拉起 PostgreSQL
- [ ] PostgreSQL 建表与索引脚本
- [ ] Mongo 事件集合与 TTL/归档策略
- [ ] MinIO bucket 与生命周期策略
- [ ] Qdrant collection 初始化脚本
- [ ] 环境变量模板与启动脚本

## 8. 验收清单（阶段完成标准）

- [ ] 文档入库链路端到端可运行并可观测
- [ ] 对话链路端到端可运行并返回引用
- [ ] Canvas 发布的 DSL 可被执行层正确执行
- [ ] 所有关键接口具备 tenant-aware 校验
- [ ] 失败任务支持重试、可追踪错误原因
- [ ] 三类后台均有最小可用页面
- [ ] 运行指标可查看（任务量、成功率、时延、成本）
- [ ] 任一 run / task 均可通过 `trace_id` 追溯完整执行路径

## 9. 风险与前置约束

- 风险：多后台并行开发导致接口频繁变更
- 约束：先冻结 API 契约，再进入大规模页面开发
- 风险：Worker 幂等未落地导致重复执行
- 约束：任务幂等键与状态机先行
- 风险：Canvas 与 DSL 演进不一致
- 约束：DSL schema version 必须强约束与迁移脚本
