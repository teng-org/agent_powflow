# Product Vision v2 - Personal Knowledge Base + Agent Platform

## 1. 结论先行

你之前让 AI 规划师给出的方案总体方向是正确的，核心思路（前后端分离、Control Plane/Execution Plane 分离、多租户、任务状态同步、Canvas 只做视图）与诉求高度一致。

但它还不够“可直接实施”，主要缺口在于：

- 缺少明确的边界上下文（哪些能力归 Spring，哪些归 Python，哪些是共享域）。
- 缺少高可用与故障策略的工程细节（重试、幂等、死信、补偿、降级）。
- 缺少 Agent Canvas 的“定义模型 + 编译模型 + 版本治理模型”完整闭环。
- 对多租户隔离停留在字段级描述，未明确隔离级别和演进路径。
- 缺少可量化的 SLO/容量目标，难以指导部署和压测。

本文件给出一套更完整、可执行的架构规划。

## 2. 目标与范围

### 2.1 产品目标

构建可私有化部署的“个人/团队知识库 + Agent 平台”，满足：

- 文档接入、检索增强生成（RAG）、会话式问答。
- 通过 Agent Workflow 实现可定制能力编排。
- 面向企业环境支持权限、审计、配额、成本与多租户。
- 保持模块插拔和版本可迁移，降低长期维护成本。

### 2.2 非目标（当前阶段不做）

- 不在 MVP 做跨地域双活。
- 不在 MVP 做复杂低代码市场生态（先支持官方模板/包）。
- 不在 MVP 做无限制动态图循环工作流（先以 DAG 为主）。

## 3. 核心设计原则

- Definition First：运行时以 DSL/配置版本为唯一真相，Canvas 仅作为可视化编辑器。
- Control/Execution 分离：Spring Boot 管理业务与治理，Python 负责 AI 执行与编排。
- 事件驱动：长链路异步化，所有步骤可观测、可回放、可追责。
- Tenant-First：所有对象默认 tenant-scoped，显式声明跨租户能力。
- 可插拔优先：模型、向量库、工具、解析器均通过 Provider 接口接入。

## 4. 分层与上下文划分（建议落地方式）

### 4.1 Presentation Layer

- `apps/web-app`：终端用户（知识库检索、对话、个人工作流）。
- `apps/web-admin`：租户管理员（用户、权限、配额、模型/工具治理、审计）。

### 4.2 Control Plane（Spring Boot）

职责：

- 身份认证与 RBAC。
- 租户、用户、知识库元数据、文档元数据管理。
- Task/Run 权威状态与审计。
- 配置版本管理（workflow/config version registry）。
- 计费口径、配额、Feature Flags、License/Entitlements。

不负责：

- 具体 AI 推理执行、向量化流水线执行、工具调用细节。

### 4.3 Execution Plane（Python）

职责：

- 文档处理流水线（parse/chunk/embed/index）。
- 检索与 rerank。
- Agent Runtime（DSL 编译后执行计划运行）。
- Tool Adapter 执行。

组件：

- `execution-api`（FastAPI，可选）用于同步入口与调试入口。
- Celery workers：按任务类型分队列与弹性扩缩容。

### 4.4 Data Plane

- MongoDB：权威业务元数据（含 Task/Run 状态）。
- Object Storage：原始文档、解析产物、大文本片段。
- Vector DB：检索索引。
- Broker（RabbitMQ 推荐）：异步任务调度。

## 5. 关键业务域模型

建议按以下域拆分（避免后期“巨石服务”）：

- IAM 域：tenant/user/role/permission/policy。
- Knowledge 域：kb/document/chunk/index/data source。
- Orchestration 域：task/task_event/run/run_step。
- Agent 域：workflow_dsl/workflow_version/agent_profile/tool_binding。
- Governance 域：feature_flag/quota/license/audit/billing_record。

## 6. Agent Canvas 正确设计（重点）

你的想法是对的：

- 图形化只是编辑器与运营工具，不应成为运行时依赖。
- 真正可迁移的是 DSL + 版本快照 + 依赖声明。

### 6.1 双模型存储

- `workflow_definition`：可执行 DSL（唯一真相）。
- `workflow_layout`：节点坐标、分组、注释、折叠状态（可丢失，不影响执行）。

### 6.2 执行路径

1. Canvas 编辑后生成 DSL。
2. Control Plane 做 schema 校验和静态分析（环检测、输入输出类型、权限检查）。
3. 发布版本（immutable）。
4. Runtime 拉取指定版本 DSL，编译为执行计划（Execution Plan）。
5. Run 持久化快照：`dsl_version + resolved providers + model/tool versions`。

### 6.3 迁移保障

- 导入导出以 DSL 为核心，layout 为可选附件。
- 保证同 DSL 在不同环境执行结果差异可追踪（记录 provider/version）。

## 7. 多租户与隔离策略

建议分三层，按阶段演进：

- L1（MVP）：共享集群 + 逻辑隔离（所有集合强制 tenant_id + 索引约束 + 应用层策略）。
- L2（Growth）：按租户分命名空间（向量库 namespace、对象存储 bucket 前缀、队列路由键）。
- L3（Enterprise）：高价值租户独享资源池（独立 worker pool/独立数据库实例）。

必须实现：

- JWT 中携带 tenant context。
- 所有查询默认追加 tenant filter（禁止“裸查询”）。
- 审计日志包含 tenant_id, actor_id, trace_id。

## 8. 高可用与可靠性设计

### 8.1 服务高可用

- Control Plane 与 Execution API 无状态多实例。
- Worker 水平扩容，按队列设置并发上限与资源池。
- Mongo 副本集，Broker 持久化队列。

### 8.2 任务可靠性

- 至少一次投递 + 幂等消费者。
- Task 状态机：PENDING -> RUNNING -> SUCCEEDED/FAILED/CANCELLED。
- 明确超时、重试、死信队列（DLQ）与人工重放入口。
- 心跳与租约机制：worker 失联后任务可回收。

### 8.3 一致性策略

- 业务权威状态仅由 Control Plane 持久化。
- Execution Plane 通过事件回传更新状态。
- 使用 outbox/inbox 模式降低“写库成功但消息失败”的不一致风险。

## 9. 模块插拔设计（Plugin/Provider）

定义稳定接口：

- `EmbeddingProvider`
- `LLMProvider`
- `RetrieverProvider`
- `ParserProvider`
- `ToolProvider`

每个 provider 具备：

- metadata（能力声明、版本、所需密钥、成本模型）。
- healthcheck。
- timeout/retry/fallback 策略。
- 租户级启用/禁用与配额策略。

## 10. 状态同步与前端显示

推荐模式：

- 写模型：Control Plane REST API。
- 读实时流：SSE（任务与运行日志），必要时 WebSocket。
- 前端状态聚合：`task timeline + run timeline + cost timeline`。

前端至少展示：

- 当前阶段（stage）、进度（progress）、最新日志。
- 失败原因（error code + message + retryable）。
- 追踪标识（task_id/run_id/trace_id）。

## 11. 安全与合规基线

- Secret 不入库，统一走环境变量或密钥管理服务。
- 敏感字段加密（如第三方 API key）。
- 上传文件类型白名单与恶意内容扫描接口预留。
- 审计日志不可篡改策略（最少保证 append-only + 管理员可导出）。

## 12. 可观测性与 SLO（建议值）

先设可执行目标，再压测修正：

- API 可用性：99.9%（月）。
- 任务状态延迟：P95 < 3s（事件上报到前端可见）。
- 文档入库时延：10MB PDF，P95 < 120s（基础 OCR 关闭）。
- 检索接口：P95 < 800ms（不含大模型生成）。

三件套：

- 日志：结构化 JSON（必带 tenant_id/task_id/run_id/trace_id）。
- 指标：QPS、错误率、队列积压、worker 利用率、token/cost。
- 追踪：跨 Spring/Python 的 trace 贯通。

## 13. 分阶段实施路线（可执行）

### Phase A - MVP（6-10 周）

- 登录/RBAC/单租户模式（保留多租户字段）。
- KB/Document 上传与入库链路跑通。
- 基础 RAG 对话（单模型、单检索策略）。
- Task/Run 状态同步 + SSE。
- DSL v0 + Canvas v0（DAG、8类节点、发布版本）。

### Phase B - Beta（8-12 周）

- 多租户策略落地（L1 完整）。
- Provider 插拔与租户级配置。
- 灰度与 feature flag。
- 成本统计与配额。
- 失败重放与 DLQ 运维工具。

### Phase C - GA（持续）

- 企业级隔离（L2/L3 选配）。
- 包签名、许可证、能力包市场。
- 审计报表与合规增强。

## 14. 对现有方案的修正建议

保留：

- 技术栈与总分层方向。
- DSL 为真相、Canvas 为视图。
- Task/Run 统一审计口径。

需要补强：

- 边界上下文与职责矩阵。
- 任务一致性与故障恢复机制。
- 租户隔离分级方案。
- 可观测与 SLO 指标闭环。
- 发布治理（版本、回滚、灰度）。

## 15. 已确认产品基线（企业优先）

以下决策已确认，作为 v1 规划与实施边界：

1. 用户优先级：企业优先，个人作为试用/自部署入口。
2. 私有化与离线：v1 支持私有化，但不强制完整 air-gapped。
3. 合规策略：先做合规能力（RBAC/审计/隔离），暂不做认证项目。
4. 多租户：MVP 接受 L1 逻辑隔离，要求全链路 tenant-aware。
5. 模型策略：架构支持双栈，MVP 先落地 OpenAI-compatible。
6. 向量库：Qdrant 作为默认主选型。
7. Broker：RabbitMQ 作为默认主选型。
8. 规模假设：中小企业规模（并发与吞吐按 SME 基线设计）。
9. Canvas 范围：MVP 仅支持 DAG，不支持循环/复杂状态机。
10. 商业化：v1 上最小 license 能力，不做复杂自动计费。

已通过 ADR 固化：`docs/decisions/ADR-001-product-scope-v1.md`。

---

## 16. 下一步执行建议

按上述基线继续产出以下落地文档：

- `docs/design/architecture/architecture-v1.md`：组件图、时序图、边界职责矩阵。
- `docs/design/roadmap/implementation-plan.md`：按周里程碑、验收标准、风险清单。
- `docs/decisions/ADR-002-domain-boundary-and-canvas-execution.zh-CN.md`：前后台域划分与 Canvas 执行边界。
- `docs/decisions/ADR-003-tenant-isolation-v1.md`：L1 隔离实现约束与红线。
- `docs/decisions/ADR-004-runtime-and-queue-topology.md`：Celery + RabbitMQ 队列模型。
- `docs/decisions/ADR-005-workflow-dsl-and-canvas.md`：DSL 真相 + Canvas 视图规范。

---

## 17. 范围澄清补充

针对“企业/个人小团队快速搭建、前台对话 + 多后台分治、Canvas 数据流导向、模块库支撑、执行层生效、多端 API 扩展”的进一步说明，已补充在：

- `docs/requirements/product-scope-clarification-v1.zh-CN.md`
