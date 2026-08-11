# design

新增：`docs/design/real-provider-contract-gate-v1.zh-CN.md`：真实商业 Provider 的显式付费授权、单次费用上限、协议/用量/限流头校验、脱敏报告与供应商账号关闭条件。

新增：`docs/design/document-move-cross-store-repair-v1.zh-CN.md`：Phase 2G 文档移动的 SQL 原子迁移、`MOVING` 检索屏障、`MOVE_DOCUMENT_POINTS` 持久修复、单项管理重试、Console 展示与真实 Qdrant 故障恢复验收。

新增：`docs/design/control-plane-kb-write-retirement-v1.zh-CN.md`：Phase 2F 文档/分块冻结与草稿 Control 代理、Execution 公共写 410 默认门禁、旧 KB Admin legacy profile、统一 Console 与三存储严格清零验收。

新增：`docs/design/control-plane-non-index-upload-authority-v1.zh-CN.md`：Phase 2E 非索引上传的 Control Task、持久完成事实、不可变暂存身份、崩溃超时恢复、Console 多文件模式选择与三存储严格清零验收。

新增：`docs/design/control-plane-document-batch-authority-v1.zh-CN.md`：Phase 2D 文档批次父子 Task、聚合状态、失败重试、精确预检、collection 并发锁、孤儿清理与三存储严格清零验收。

新增：`docs/design/control-plane-document-command-authority-v1.zh-CN.md`：Phase 2C 单文档/KB 命令的 Control Task/Outbox 权威、Worker 回写、摘要重试、移动对象元数据与 MinIO 全版本删除、真实严格清零验收。

新增：`docs/design/control-plane-multipart-upload-authority-v1.zh-CN.md`：Control multipart 上传意图、两阶段对象暂存、引用派发、超时恢复、持久精确清理与真实全栈验收。

新增：`docs/design/control-plane-task-ingest-outbox-v1.zh-CN.md`：Control 创建文本入库 Task、事务 Outbox、Execution 内部派发、Worker 幂等回写、死信查询/重放、V11/V12 生命周期约束与真实全栈验收。

新增：`docs/design/console-accessibility-interaction-v1.zh-CN.md`：Console 登录、导航、自定义下拉、焦点、动效降级与三档响应式浏览器验收记录。

新增：`docs/design/approval-alert-delivery-outbox-v1.zh-CN.md`：审批临期/逾期/超时告警事实、Webhook Outbox、重试/死信/重放、安全目标约束、Console 与全栈验收。
新增：`docs/design/approval-retained-audit-trend-v1.zh-CN.md`：独立于 Run 的审批事实、角色过滤 UTC 趋势、保留与响应式验收。

新增：`docs/design/durable-human-approval-runtime-v1.zh-CN.md`：人工审批节点的 DSL、持久化暂停/恢复、权限、幂等、超时、Console 交互与全栈验收边界。

新增：`docs/design/control-plane-run-event-lifecycle-v1.zh-CN.md`：Conversation、Message、Run、Event 权威状态机、内部回写、幂等与 SSE 契约。
新增：`docs/design/execution-workflow-dispatch-v1.zh-CN.md`：Control 到 Execution 的 at-least-once 投递、Celery 回写与零成本 mock Workflow 执行。
新增：`docs/design/transactional-outbox-execution-lease-v1.zh-CN.md`：Run 事务 Outbox、并发安全领取、执行租约/心跳、指数退避、死信与管理员重放。
新增：`docs/design/provider-idempotency-active-cancel-dlq-console-v1.zh-CN.md`：真实 Provider 业务幂等、租约丢失主动中断、RabbitMQ DLQ 持久化/审计/Console 重放。
新增：`docs/design/workflow-graph-runtime-v1.zh-CN.md`：DAG 拓扑执行、条件分支、多 LLM 传值、发布/运行双重校验和逐节点事件。
新增：`docs/design/console-workflow-management-v1.zh-CN.md`：统一 Console 的 Workflow 列表、草稿编辑、服务端校验、不可变版本、归档删除和响应式验收。
新增：`docs/design/parallel-fanout-join-runtime-v1.zh-CN.md`：结构化并行 fork/join、分支状态隔离、确定性合并/失败、并发事件审计与真实全栈验证。

新增：`docs/design/workflow-node-adapters-retrieval-http-v1.zh-CN.md`：统一节点适配器、租户隔离检索、标准引用传播，以及默认拒绝、SSRF 防护、幂等和主动中断的 HTTP 工具。
新增：`docs/design/workflow-node-retry-attempts-v1.zh-CN.md`：节点级显式重试、故障分类、可中断退避、attempt 权威持久化与 Console 审计展示。
新增：`docs/design/execution-task-store-invariants-integration-tests-v1.zh-CN.md`：任务身份/状态/进度不变量、租户隔离、Worker 重投递门禁和隔离 PostgreSQL 集成测试。
新增：`docs/design/execution-postgresql-schema-migrations-v1.zh-CN.md`：Execution PostgreSQL V1–V9 显式迁移、校验和、失败账本、并发互斥和启动门禁。
新增：`docs/design/control-plane-flyway-core-schema-v1.zh-CN.md`：Control Plane V1–V13 完整 Flyway 基线、baseline 0 共享 schema 接管与 Hibernate validate。
新增：`docs/design/control-plane-knowledge-authority-v1.zh-CN.md`：Control Plane 接管知识库元数据与租户绑定、共享 schema 过渡、内部物理删除协议和后续 Document/Task 权威迁移边界。
新增：`docs/design/control-plane-task-operational-authority-v1.zh-CN.md`：Control Plane 接管 Task 查询、取消、批次级联与历史清理，Execution 内部 revoke 协议及 Task 创建/状态回写的 Phase 2 边界。
新增：`docs/design/chat-postgresql-schema-migrations-v1.zh-CN.md`：Chat PostgreSQL V1/V2 显式迁移、遗留表接管、checksum/失败账本、并发互斥和启动门禁。
新增：`docs/design/chat-sse-run-reliability-v1.zh-CN.md`：Chat 主动取消、Provider 双失败、TCP/SSE 断线、终态不可逆与并发事件隔离。
新增：`docs/design/provider-tool-fault-sandbox-v1.zh-CN.md`：真实本地 HTTP/SSE 故障沙箱、统一重试分类/退避、Tool 幂等窗口与第三方账号验收边界。
新增：`docs/design/document-index-cross-store-consistency-v1.zh-CN.md`：文档 PostgreSQL/MinIO/Qdrant 发布屏障、整代幂等发布、删除修复账本、统一告警/Prometheus 与真实故障注入矩阵。
新增：`docs/design/document-object-storage-minio-v1.zh-CN.md`：文档原件/解析文本对象协议、校验和、队列引用、删除补偿、V4 迁移、MinIO 治理检查与运维边界。
新增：`docs/design/object-storage-capacity-governance-v1.zh-CN.md`：租户逻辑配额、并发容量预留、V5/V6 迁移、文本大小门禁、Prometheus/告警/Console 和物理容量边界。

模块设计、数据结构、协议与 DSL 设计文档。

- `docs/design/minio-physical-capacity-observability-v1.zh-CN.md`：MinIO 官方物理容量/节点/驱动指标、Prometheus/Alertmanager、V7 告警事实、Secret 边界与双 webhook 验收。
- `docs/design/minio-document-bucket-governance-v1.zh-CN.md`：文档 bucket 版本化、幂等生命周期、物理容量写入准入、非 root Execution 镜像与真实版本验收。
- `docs/design/minio-service-identity-tls-v1.zh-CN.md`：MinIO Application/Governance/Metrics 最小权限身份、HTTPS/CA 信任链、生产门禁与真实权限验收。

## 当前文档
- `docs/design/current-phase-construction-checklist-v1.zh-CN.md`：当前阶段施工清单（响应流、数据流、实例、DB 字段、技术选型）。
- `docs/design/kb-singleton-admin-implementation-v1.zh-CN.md`：知识库产品化实现方案（多库 + 管理员专用）与接口约定。
- `docs/design/kb-audit-logging-v1.zh-CN.md`：知识库企业级审计日志方案（结构、脱敏、按天落盘、权限与错误追踪）。
- `docs/design/chat-memory-infrastructure-design-v1.zh-CN.md`：Chat 记忆基础设施详细设计（画像/活动、会话级 RAG、融合检索、生命周期与迁移）。
- `docs/design/powflow-console-implementation-checklist-v1.zh-CN.md`：Powflow Console 施工清单（全量接口映射、页面拆解、里程碑与验收；接口数量以自动审计为准）。
- `docs/design/control-plane-workflow-lifecycle-v1.zh-CN.md`：Workflow 草稿、DSL 校验、不可变发布版本、乐观并发与租户隔离实现。
