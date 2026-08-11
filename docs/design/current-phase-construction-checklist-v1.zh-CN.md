# 当前阶段施工清单 v1（MVP / 企业优先）

## 2026-08-08 Task Authority Phase 2A / 2B / 2C / 2D / 2E / 2F / 2G 实施复核

- [x] Control 创建文本入库 Task、`TASK_CREATED` 事件与事务派发 Outbox。
- [x] Execution 内部派发校验 Task 不可变身份、终态和文档去重锁。
- [x] Worker 对 `state_owner=CONTROL` 的任务通过内部事件接口回写，事件幂等、状态合法、进度单调。
- [x] Console 文本入库入口由 Execution 切换到 Control。
- [x] Task 派发 Outbox 支持租户隔离查询、死信重放、退避和耗尽终态。
- [x] Flyway V11/V12 覆盖事件幂等、Outbox 及 Task 删除级联，三种真实 PostgreSQL 升级路径通过。
- [x] 两次真实全栈文本入库达到 `SUCCEEDED / CONTROL / PUBLISHED / INDEXED`；最终严格清理为零残留。
- [x] multipart 文件上传命令迁移到 Control；索引模式采用 `WAITING_UPLOAD → 暂存 → 激活派发` 两阶段协议，Outbox 不保存二进制。
- [x] 上传意图超时、激活失败与清理失败进入 `CLEANUP_PENDING`，恢复器只在 Execution 精确清理成功后收敛为 `CANCELLED`。
- [x] Console multipart 路由切换到 Control；真实文件全栈达到 `SUCCEEDED / CONTROL / PUBLISHED / INDEXED`，PostgreSQL、MinIO、Qdrant 和测试 schema 严格清零。
- [x] KB/文档摘要、分块、重索引、草稿提交、移动、标签和删除改为 Control Task/事件/事务 Outbox，Console 写入口同步切换。
- [x] 文档移动保留对象定位元数据；显式删除清除全部 MinIO 对象版本，真实“移动后删除”严格清零。
- [x] 批处理父子 Task/`task_batches` 已迁移到 Control：原子创建、精确预检、聚合状态、取消计数、失败项新批次重试、Console 详情与操作闭环。
- [x] 并发批量移动通过 collection 级 PostgreSQL advisory lock 串行化 Qdrant 写入；KB 删除补齐批次、去重锁和摘要缓存清理。
- [x] 非索引 `UPLOAD_ONLY / UPLOAD_AND_CLEAN` 统一创建 `DOC_UPLOAD` Control Task；成功收敛为 `COMPLETED` 持久事实，暂存后崩溃由 `WAITING_UPLOAD → CLEANUP_PENDING` 超时恢复。
- [x] Execution 暂存接口对全部模式强制校验 Control Task 不可变身份；Console 支持三模式、多文件、数量反馈和无效选项隐藏。
- [x] 文档/分块冻结与分块草稿保存/清除迁移为 Control 公开接口和租户约束的 Execution 内部接口；Console 不再直接写 Execution。
- [x] Execution 公共 KB/文档/Task 写接口默认返回 `410 LEGACY_KB_WRITE_RETIRED`，读取和非 KB 管理接口不受影响；兼容实现只可由显式环境变量短期开启。
- [x] 旧 `kb-admin-web` 进入 `legacy-kb-admin` Compose profile，默认服务清单排除，历史运行容器已停止。
- [x] 文档移动在同一 PostgreSQL 事务内写入目标归属、`MOVING` 发布屏障和 `MOVE_DOCUMENT_POINTS` 修复义务；Qdrant 收敛成功后才恢复原发布状态。
- [x] Control 重试可识别已迁入目标库的待修复文档；Execution 提供单项修复 API，Console 展示移动上下文并支持失败项精确重试。
- [x] 当前验证：Control 110/110、Execution 155/155（真实隔离 PostgreSQL + TLS MinIO，0 跳过）、Console 55/55、内部契约 14/14、端点目录 288/288；Qdrant 停机移动 smoke 验证持久修复、幂等重试、对象引用保持与三存储严格清零。
- [x] 真实 Provider 契约执行器已隔离于默认 smoke，具备显式付费授权、环境变量密钥、HTTPS 公网、非零价格、调用前/后费用上限和脱敏报告门禁；本地测试 5/5。
- [x] Chat API Pool 修复 OPEN 端点兜底回选；HALF_OPEN 单探针、成功关闭、失败重开、取消释放及重启清除瞬时探针声明测试 5/5，完整 Chat 39/39（真实隔离 PostgreSQL，0 跳过，临时 schema 残留 0）；Console 健康表已展示三态熔断、并发和失败指标，55/55 与生产构建通过。
- [x] PostgreSQL/MongoDB/Qdrant/MinIO 隔离恢复编排为统一 backup set；总 manifest 强制核对 4 个归档字节数/SHA-256 和存储专属恢复/清理事实，并明确 71.319 秒 `online_fuzzy_window`、非原子及恢复前静默写入/恢复后对账要求。
- [x] MinIO 版本化备份使用最小权限应用身份和受治理文档 bucket 的随机隔离前缀，3 个逻辑对象/5 个物理版本逐版归档恢复；私有 CA、非 root 临时文件清理与全版本零残留已实测。
- [x] 隔离 128 MiB tmpfs MinIO 真实触发 `XMinioStorageFull`：31×4 MiB 成功写基线、释放后恢复写入/复读、临时资源零残留且正式 MinIO 全程 running。
- [ ] 使用专用供应商测试账号运行 Chat/Embedding 契约，核对供应商控制台账单、限流头、请求 ID、熔断半开恢复与告警 Webhook；未经显式费用授权不得执行。

- 版本：v1
- 日期：2026-02-09
- 阶段：MVP（Phase A）
- 依据：
  - `docs/decisions/ADR-001-product-scope-v1.zh-CN.md`
  - `docs/decisions/ADR-002-domain-boundary-and-canvas-execution.zh-CN.md`
  - `docs/requirements/product-scope-clarification-v1.zh-CN.md`

## 0. 2026-08-03 实施复核

本文件早期章节保留 MVP 目标态，以下为当前源码与运行态的权威复核：

- [x] 13 服务 Compose 栈可运行，Control/Execution/Chat 健康与四前端构建通过；
- [x] 文档上传/文本入库 → MinIO 原件与解析文本 → Celery 对象引用 → PostgreSQL 分块/向量副本 → Qdrant 整代发布 → `INDEXED` 检索闭环；
- [x] 文档/KB 删除后的 Qdrant/MinIO 失败义务进入持久修复账本，并具备 Worker、管理 API、Console 三类入口；
- [x] Conversation/Message/Run/Event、Workflow 草稿/版本、事务 Outbox、执行租约、节点 attempt、取消、两类死信与审计重放闭环；
- [x] Retrieval、默认拒绝的安全 HTTP、Workflow Secret、节点重试与证据引用闭环；
- [x] 统一 Console 已补齐 Workflow 列表、草稿编辑、服务端校验、不可变版本审计、归档/删除和乐观锁冲突保护；
- [x] 结构化 `parallel.fork/join` 已支持 2–8 个真实并发分支、状态隔离、确定性主输出/失败聚合、节点审计与全栈 smoke；
- [x] Control Flyway V1–V13、Execution V1–V9、Chat V1/V2 显式迁移与启动门禁；
- [x] 当前验证：Control 110/110、Execution 155/155（均为真实隔离 PostgreSQL、无跳过；Execution 含 TLS MinIO）、Console 55/55、内部契约 14/14、端点目录 288/288；
- [x] `human.approval` 持久化暂停/恢复、角色审批、乐观锁幂等、超时决定、Console 审批台与全栈审批 smoke 已闭环；
- [x] 审批列表改为数据库侧角色过滤后再限流，补齐积压/临期/逾期/超时率/处理时长 SLA 摘要和隔离 schema 集成测试；
- [x] 审批事实通过 Flyway V7 独立于 Run 保留，补 1–90 天角色过滤趋势 API、Console 7/14/30/90 天展示、删除 Run 后保留测试和桌面/窄屏浏览器验收；
- [x] 审批临期/逾期/超时告警通过 Flyway V8 独立保留，补幂等评估、确认/自动解决、Webhook Outbox、并发领取、指数退避/死信/重放、SSRF 防护、Console 和真实全栈 smoke；
- [x] 真实 MinIO/Qdrant 停止/恢复演练、修复健康统计与 stale `IN_PROGRESS` 自动回收；
- [x] 修复健康接入统一告警预览、Console 总览和 Prometheus 文本指标；MinIO 版本化/生命周期/TLS 可检查；
- [ ] 生产化剩余：持续饱和/多节点压力与告警提前量、跨存储原子 checkpoint、WAL/PITR 与异地不可变备份、外部值班渠道/供应商模板联调、第三方 Provider 测试账号实测与账单核对、高级审批（委派/会签/附件/升级策略/长期不可变归档）、嵌套/跨 Worker 并行、Vault/KMS、Secret 年龄告警、三平面拆 schema/最小权限。

具体证据与未完成项以 `docs/audits/current-implementation-audit-2026-08-02.zh-CN.md`（最近复核 2026-08-08）为准。

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
- 指标：跨存储修复已提供 Prometheus 文本指标；完整服务指标与 Grafana/Alertmanager 仍为后续
- 链路追踪：OpenTelemetry（建议）
- 部署：Docker Compose（当前），K8s（后续）

## 7. 施工任务清单（可执行）

## 7.1 Control Plane（Spring）

- [ ] 建立项目骨架与模块分包（iam/knowledge/orchestration/agent/governance）
- [ ] JWT + RBAC + tenant context 注入
- [x] KB 元数据/可见性/冻结/租户绑定权威 API（Flyway V9；Console 已迁移；物理删除委派 Execution 内部接口）
- [x] 三种 multipart 模式统一使用 Control Task + MinIO 两阶段上传意图；非索引成功与超时清理事实均持久化
- [x] Run 权威状态、Internal Event 与 SSE 事件流接口
- [x] Task 运营权威 Phase 1：Control 查询/事件/取消/批次级联/历史清理，Execution internal revoke
- [x] Task 权威 Phase 2A：文本入库由 Control 创建 + Outbox，Worker Internal Event 回写，死信查询/重放与清理闭环
- [x] Task 权威 Phase 2C：单文档摘要、分块/重索引、草稿提交、移动、标签、删除及 KB 摘要迁移
- [x] Task 权威 Phase 2D：批处理父子任务、聚合/取消/失败重试、Console 与严格清理
- [x] Task 权威 Phase 2E：非索引上传完成事实、崩溃超时清理、Console 三模式与多文件
- [x] Phase 2 后续：Execution 兼容入口默认 410，旧前端退役到显式 legacy profile
- [x] PostgreSQL schema + migration（Flyway V1–V13 + Hibernate validate）
- [x] Execution PostgreSQL 显式迁移（V1–V9 + checksum/失败账本/并发锁/启动门禁，含对象元数据、跨存储修复账本和批次取消计数）
- [x] Chat PostgreSQL 显式迁移（V1/V2 + 遗留表接管 + Store fail-closed）

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
- [x] PostgreSQL 建表与索引脚本（三平面均由显式版本迁移接管）
- [ ] Mongo 事件集合与 TTL/归档策略
- [x] MinIO bucket 版本化与固定 ID 生命周期策略（当前对象不自动过期，非当前版本到期且保留最近版本）
- [ ] Qdrant collection 初始化脚本
- [ ] 环境变量模板与启动脚本

## 8. 验收清单（阶段完成标准）

- [x] 文本文档入库链路端到端可运行并可观测（Control Task/Outbox/事件 + Execution 文档索引）；multipart 仍待迁移
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

## 10. 2026-08-03 数据保护补强记录

- [x] 新增 PostgreSQL custom-format 全库备份脚本。
- [x] 每次备份先执行 archive list 校验，再恢复到随机隔离数据库。
- [x] 自动核对 70 张 public 表、Control/Execution/Chat 三套迁移账本和关键业务表。
- [x] 生成带 SHA-256、字节数、归档对象数和清理结果的 manifest。
- [x] 清理隔离恢复数据库与容器临时归档，并独立复查残留为 0。
- [ ] 生产环境 WAL 归档与时间点恢复（PITR）。
- [ ] 加密、异地、不可变备份与自动保留策略。
- [x] MongoDB、MinIO、Qdrant 与 PostgreSQL 的统一 backup set 编排、组件恢复验收及 SHA-256 二次校验。
- [x] MongoDB 全库 gzip archive 隔离恢复，8 个集合/916 条文档逐集合核验。
- [x] Qdrant 原生 snapshot 下载/upload 隔离恢复与 point/payload 核验。
- [x] MinIO 3 个 canary 对象/5 个物理版本归档恢复、逐版本 SHA-256 核验与全版本严格清零。
- [x] 为四类存储引入统一 backup set ID、组件相对路径、恢复事实和非原子时间窗声明。
- [ ] 引入写入静默/跨存储原子 checkpoint、生产恢复编排和恢复后自动对账。

## 11. 2026-08-03 Secret 治理记录

- [x] 生产启动新增独立 `.env.production` 和 16 项 fail-closed 门禁。
- [x] PostgreSQL、MongoDB、RabbitMQ、MinIO、Qdrant 与三平面连接改为环境变量注入。
- [x] 校验 Secret 长度、占位符、URL-safe、Fernet 格式和跨用途复用。
- [x] 新增安全随机生产 env 生成器，默认拒绝覆盖已有文件。
- [x] CI 新增私钥/Token/高熵字面量/凭据 URI/运行时 env 文件扫描及单测。
- [x] 运行态 API Pool 和原始 debug log 路径加入忽略，防止 Provider Key 误提交。
- [x] Workflow Fernet 主/旧密钥双读、新写主密钥、事务批量重加密和反向回滚流程。
- [x] Control/Execution 标准 HS256 JWT、独立 `kid/iss/aud` key ring、最多 3 个 previous keys 和最长 7 天旧格式兼容窗口。
- [x] Chat 仅信任 Control key ring；跨平面令牌真实 HTTP 拒绝验证通过。
- [x] Refresh token 单次轮换、数据库行锁、旧 token 撤销/重放拒绝及新 refresh token 返回。
- [x] Stack Smoke 从 Execution 健康响应核验 `embedding_mode`，默认在业务数据创建前拒绝外部 Embedding，防止回归测试意外计费。
- [ ] Vault/KMS 运行时集成，以及旧 key slot 使用率、Secret 年龄和轮换逾期告警。

## 12. 2026-08-03 对象存储容量治理记录

- [x] Execution V5 增加 `extracted_object_size_bytes` 和租户在途容量预留表。
- [x] Execution V6 回填历史解析对象 UTF-8 字节数，不修改已执行迁移 checksum。
- [x] 上传前按源文件与最终解析文本精确字节做租户级配额准入。
- [x] PostgreSQL tenant advisory transaction lock 防止并发请求共同越过配额。
- [x] 成功、失败均释放预留；进程异常残留由 30–3600 秒 TTL 自动回收。
- [x] `UPLOAD_AND_CLEAN` 改为清洗后一次性持久化，消除二次覆盖容量窗口。
- [x] JSON 文本入库增加 UTF-8 字节上限，并在 KB/任务锁写入前拒绝。
- [x] 超配额在 MinIO 写入前返回 `507 OBJECT_STORAGE_QUOTA_EXCEEDED`；批量上传保留逐文件结构化失败详情。
- [x] 对象健康接口、Prometheus、统一告警预览和 Console 使用同一容量快照。
- [x] 生产配置门禁要求正整数租户配额；开发环境可显式使用 0 关闭。
- [x] 并发双请求、过期回收、禁用配额、历史回填、API 拒绝和告警状态均有真实 PostgreSQL 测试。
- [x] 接入 MinIO 官方物理磁盘/节点 Prometheus 指标和真实 Alertmanager 接收器；V7 持久化 firing/resolved 事实，双 webhook 冒烟通过。
- [x] 完成 16 MiB 单对象、16 路并发、同键 10 版本的 TLS 有界压力基线，全部版本严格清零。
- [x] 在有界 tmpfs 隔离 MinIO 中执行单节点真实磁盘满拒绝与释放恢复测试。
- [ ] 执行持续饱和、多节点和生命周期扫描期间的生产拓扑物理压力测试。
- [ ] 将统一租户配额演进为套餐/例外配置与审计化调额流程。

## 13. 2026-08-03 MinIO 身份与 TLS 收口记录

- [x] root、Application、Governance、Metrics 身份边界拆分，Execution 不再持有 root 凭据。
- [x] 应用策略限定文档 bucket 对象操作，并仅增加健康检查所需的 versioning/lifecycle 只读权限。
- [x] 治理策略只允许文档 bucket 的 versioning/lifecycle 管理，真实对象读取被拒绝。
- [x] MinIO HTTPS、Execution CA 强校验和 Prometheus HTTPS 抓取已在真实 Compose 栈通过。
- [x] 开发证书生成器、Compose TLS overlay、启动/停止/重启开关贯通。
- [x] 生产启动强制 Observability + TLS；门禁拒绝明文、身份复用和遗留共享凭据。
- [x] Execution 128/128 在真实 PostgreSQL + MinIO TLS 下通过；最小权限 smoke 无对象版本残留。
- [ ] 接入生产 PKI 自动签发/续期/吊销与双证书轮换。
- [ ] 将 root 及运行凭据迁入 Vault/KMS/云 Secret Manager，并补 Secret 年龄与轮换逾期告警。
- [x] 完成有界大对象、并发与版本膨胀冒烟，Prometheus HTTPS target 保持健康且无 firing 告警。
- [x] 完成隔离单节点磁盘满拒绝、释放后恢复和正式 volume 零影响演练。
- [ ] 完成持续饱和、多节点和版本化生命周期扫描压力演练。
