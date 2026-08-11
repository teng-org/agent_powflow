# Agent Powflow 当前实现审计（2026-08-02）

## 2026-08-08 隔离磁盘满恢复增量

新增 `tools/smoke/minio-disk-full-smoke.ps1`，使用随机临时容器和 128 MiB tmpfs 注入真实存储满，不挂载或写入正式 MinIO volume。实测先成功写入 31 个 4 MiB 对象（130,023,424 bytes），随后严格观察到 `XMinioStorageFull`；删除填充对象并复查零残留后，recovery 对象写入、复读和 SHA-256 核对成功。测试 bucket、容器与 Execution 临时 helper 全部清理，正式 MinIO 演练前后均 running。该证据关闭单节点存储满拒绝/释放恢复机制验证，但不替代持续饱和、多节点纠删码和生产告警提前量测试。完整边界见 `docs/operations/minio-isolated-disk-full-smoke-v1.zh-CN.md`。

## 2026-08-08 四存储统一备份集增量

四项既有恢复演练已由 `infra/scripts/verify-unified-backup-set.ps1` 编排为统一 backup set：PostgreSQL、MongoDB、Qdrant、MinIO 每项先完成隔离恢复和清理，再由总 manifest 二次核对归档文件存在性、字节数与 SHA-256，并强制检查各存储专属的恢复/清理标志。最终真实复跑生成 4 个组件、71.319 秒在线时间窗，PostgreSQL 73 张表/16,551 个归档对象及迁移账本 `13/9/2` 一致，MongoDB 8 个集合/916 条文档一致，Qdrant 2 个 canary points 一致；恢复库、隔离 schema 和 Qdrant canary collection 残留均为 0，输出目录越界被启动前拒绝。

MinIO 演练不再依赖已被最小权限策略禁止的临时建桶，而是在治理身份已启用 versioning 的文档 bucket 下使用随机隔离前缀；3 个逻辑对象共 5 个物理版本按旧到新归档/恢复，逐版本校验大小与 SHA-256，并按 version ID 清理后复查零残留。期间发现并修复 helper 未加载私有 CA、`docker cp` root 文件无法由非 root 服务用户清理两项旧缺陷。总 manifest 明确标记 `online_fuzzy_window`、`atomic_cross_store_snapshot=false`、恢复前需静默写入并执行跨存储对账，未把顺序在线备份误报为原子 checkpoint。完整手册见 `docs/operations/unified-backup-set-drill-v1.zh-CN.md`。

## 2026-08-08 真实 Provider 契约门禁增量

已新增独立的 `tools/provider_contract.py`，将商业 Provider 验收从默认 smoke 中隔离。执行器没有显式付费授权、环境变量密钥、非零供应商单价和正数费用上限时会在 HTTP 前失败；只允许 HTTPS 公网地址；Chat 最多生成 8 token，Embedding 使用固定短文本，不自动重试。通过后只输出模型、状态、usage、向量维数、限流/请求 ID 头名称和估算费用，不落 API Key、提示词、模型正文、向量及原始头值。

当前契约门禁验证 5/5，覆盖未授权时 DNS 零调用、空模型拒绝、调用前费用上限、成功报告脱敏和缺失 usage 失败。审计同时发现并修复 Chat API Pool 在无健康候选时回选 OPEN 端点的熔断绕过：当前 OPEN 严格阻断，HALF_OPEN 只允许一个探针，成功关闭、失败重开、取消释放；恢复持久状态时会清除只属于旧进程的探针声明，避免异常重启造成永久假占用。状态机 5/5，完整 Chat 39/39（真实隔离 PostgreSQL，0 跳过，临时 schema 残留 0）。Console 健康表同步展示三态熔断、并发与失败指标，55/55 和生产构建通过。该增量只完成了“可安全执行和可审计”的工程门禁；尚未获得用户对商业账号调用与费用的显式授权，因此没有调用任何真实 Provider，也不能把第三方 Provider 标记为生产验收完成。完整关闭条件见 `docs/design/real-provider-contract-gate-v1.zh-CN.md`。

## 2026-08-08 Task Authority Phase 2G 审计增量

文档移动已具备跨 PostgreSQL/Qdrant 的持久恢复协议。移动首先在同一 PostgreSQL 事务中完成文档、分块、标签和统计归属变更，将目标文档置为 `MOVING`，并登记 `MOVE_DOCUMENT_POINTS` 修复义务；`MOVING` 作为发布屏障，确保 Qdrant 尚未收敛时源库和目标库检索都不会暴露半成品。修复器以文档为边界幂等替换目标向量、删除源向量，成功后才恢复原始发布状态。Control 重试预检可以识别“SQL 已移动、修复仍在账本”的事实，不再因源库查无文档而阻断恢复。

Execution 新增平台管理员单项修复接口 `POST /api/v1/admin/system/cross-store-repairs/{repair_id}/execute`；统一 Console 可展示移动来源、目标、文档和原状态，并对 `PENDING/FAILED` 行执行精确重试。验证证据：Control 110/110、Execution 155/155（真实隔离 PostgreSQL + TLS MinIO，0 跳过）、Console 55/55 并通过生产构建、内部派发契约 14/14、端点目录 288/288。真实故障 smoke 停止 Qdrant，确认 Control 移动任务失败、目标保持 `MOVING`、MinIO 引用不变和修复义务持久；恢复 Qdrant 后经单项接口收敛为源 0 点、目标 2 点和 `INDEXED`，再次提交同一移动命令成功，最终 PostgreSQL/Qdrant/MinIO 全版本严格清零。完整契约见 `docs/design/document-move-cross-store-repair-v1.zh-CN.md`。

仍未完成：第三方 Provider 测试账号实测与账单核对、生产级多节点/持续饱和及告警提前量演练、高级审批协作、Vault/KMS、跨存储原子 checkpoint、PITR/异地不可变备份与 Execution 巨型模块拆分。

## 2026-08-08 Task Authority Phase 2F 审计增量

知识库公开写入口已经收口到 Control Plane。文档冻结、分块冻结、分块草稿保存/清除新增 Control 租户约束代理和 Execution 内部 token 接口；统一 Console 全部切换到 Control。Execution 公共 KB/文档/Task 写接口默认返回 `410 LEGACY_KB_WRITE_RETIRED`，只有显式设置 `EXECUTION_LEGACY_KB_WRITE_ENABLED=true` 才进入短期兼容实现。旧 `apps/kb-admin-web` 已放入 `legacy-kb-admin` Compose profile，默认服务清单不再包含，历史运行容器已停止。

验证证据：Control 110/110（真实 PostgreSQL，0 跳过）、Execution 153/153（真实隔离 PostgreSQL + TLS MinIO，0 跳过）、Console 54/54 并通过生产构建、内部派发契约 13/13、端点目录 287/287。真实退役 smoke 验证旧公开写返回 410，Control 草稿保存/清除与文档/分块冻结均可由 Execution 读取回显；删除后 PostgreSQL/Qdrant/MinIO 全版本严格清零。浏览器已确认统一 Console 展示三种上传模式、冻结和草稿操作，且无旧后台品牌。完整契约见 `docs/design/control-plane-kb-write-retirement-v1.zh-CN.md`。

Phase 2F 当时的移动外部故障修复缺口已由 Phase 2G 关闭；其余生产化缺口见本文 Phase 2G 增量。

## 2026-08-08 Task Authority Phase 2E 审计增量

`UPLOAD_ONLY` 与 `UPLOAD_AND_CLEAN` 已从无 Task 的请求期兼容路径迁移为 Control 两阶段上传意图。所有模式均先创建不可变 Control Task、`TASK_CREATED` 事件和 `WAITING_UPLOAD` 持久事实；非索引模式暂存成功后原子收敛为 `DOC_UPLOAD / SUCCEEDED / UPLOADED_RAW|CLEANED` 与 `DOC_UPLOAD_ARTIFACT / COMPLETED`，不发布 Worker 命令。暂存后 Control 崩溃会由超时恢复器转入 `CLEANUP_PENDING`，精确清理完成前不会提前取消意图。

Console 已增加三模式选择、多文件提交、接受/跳过数量反馈和文件控件复位；非索引模式隐藏无效的自动总结选项。Execution 内部暂存接口现在对全部模式强制校验 Control Task、租户/KB/文档身份、Task 类型和等待状态。

验证证据：Control 109/109（真实 PostgreSQL，0 跳过）、Execution 150/150（真实隔离 PostgreSQL + TLS MinIO，0 跳过）、Console 53/53、端点目录 279/279。真实非索引上传 smoke 完成 2 个 Control Task、2 个完成事件、2 个 `COMPLETED` 持久事实，两个文档均为 0 切片；删除后 PostgreSQL/Qdrant/MinIO 全版本严格清零。完整契约见 `docs/design/control-plane-non-index-upload-authority-v1.zh-CN.md`。

## 2026-08-08 Task Authority Phase 2D 审计增量

文档批处理已完成 Control 权威迁移：一次请求原子创建批次、父 Task、1–500 个子 Task、事件和子任务 Outbox；Execution 提供精确无正文预检并校验不可变批次身份；Control 对回调、取消、死信和重放统一聚合，部分取消不再误报成功，失败项以新批次重试。Console 已提供文档多选、六类批量表单、父子明细和失败项重试。

真实并发移动暴露并修复 Qdrant 首次建 collection 的竞争，当前源/目标 collection 写入由 PostgreSQL advisory lock 跨 Worker 串行化。KB 删除补齐 `task_batches`、`task_dedup_locks` 和 `summary_segment_cache`，Execution V1 checksum 保持不可变，新列只由 V9 引入。

验证证据：Control 105/105（Flyway V1→V13 专项 6/6）、Execution 148/148（真实 PostgreSQL + TLS MinIO，0 跳过）、Console 52/52、端点目录 279/279。真实批量 smoke 完成 2 个入库 Task、4 个批次、8 个子 Task、102 条 Execution 回调；聚合、Outbox、标签、零 overlap、移动、删除通过，PostgreSQL/Qdrant/MinIO 全版本严格清零。完整契约见 `docs/design/control-plane-document-batch-authority-v1.zh-CN.md`。

Phase 2D 当时的旧写入口/旧前端退役缺口已由 Phase 2F 关闭，移动外部故障修复缺口已由 Phase 2G 关闭；仍未完成外部摘要 Provider 稳定性联调。

## 2026-08-08 Task Authority Phase 2C 审计增量

单文档写命令已完成 Control 命令化：KB/文档摘要、分块、重索引、草稿提交、移动、标签和删除均由 Control 原子创建 Task、事件与事务 Outbox，Execution 负责资源复核、Celery 投递和副作用，Worker 统一回写 Control。Console 对应写入口已切换；`chunk_overlap=0`、标签 replace、瞬态 Provider 重试、首次回报补齐文件名均有自动测试。

真实“入库→分块→提交→标签替换→移动→删除”验收为 6 个 Control-owned Task 全成功、59 条 Execution 回调；审计过程中发现并修复了移动丢失 MinIO 元数据以及版本化对象删除仅生成 delete marker 的缺陷。最终 PostgreSQL 业务记录/孤儿 Outbox/隔离 schema、Qdrant 精确 collection、MinIO 当前对象与全部历史版本均为 0。验证证据：Control 100/100、Execution 142/142（真实 PostgreSQL + TLS MinIO，0 跳过）、Console 51/51、端点目录 275/275。

Phase 2C 当时的批处理缺口已由 Phase 2D 关闭；非索引上传持久清理缺口已由 Phase 2E 关闭；旧写入口/旧前端退役缺口已由 Phase 2F 关闭。仍未完成外部摘要 Provider 稳定性联调。完整契约见 `docs/design/control-plane-document-command-authority-v1.zh-CN.md`、`docs/design/control-plane-non-index-upload-authority-v1.zh-CN.md` 与 `docs/design/control-plane-kb-write-retirement-v1.zh-CN.md`。

## 2026-08-04 Task Authority Phase 2B 审计增量

multipart 索引上传已迁移为 Control 命令与两阶段上传意图：Control 先原子创建 `PENDING / UPLOAD_PENDING` Task、事件和 `WAITING_UPLOAD` Outbox；Execution 只暂存对象并返回校验引用；Control 验证后再激活派发。文件二进制不进入数据库，超时或失败通过 `CLEANUP_PENDING` 持久恢复，精确清理成功后才收敛为 `CANCELLED`。Console 已切换到 Control 路由。

验证证据：Control 95/95（真实 PostgreSQL、0 跳过），Execution 136/136（真实隔离 PostgreSQL + TLS MinIO、0 跳过），Console 50/50，端点目录 267/267。真实 multipart 达到 `SUCCEEDED / CONTROL / PUBLISHED / INDEXED`，21 个事件中 19 个来自 Execution 回调；清理核对 PostgreSQL 业务记录、Qdrant collection、MinIO 对象前缀、孤儿 Outbox 和隔离测试 schema 全为 0。完整契约见 `docs/design/control-plane-multipart-upload-authority-v1.zh-CN.md`。

Phase 2B 当时的摘要、分块提交、移动、标签和删除缺口已由 Phase 2C 关闭；`UPLOAD_ONLY` / `UPLOAD_AND_CLEAN` 已由 Phase 2E 纳入 Control Task、持久完成事实和超时清理，Execution 公开兼容写入口现可进入退役阶段。

## 2026-08-04 Task Authority Phase 2A 审计增量

文本入库已从“Execution 直接创建并写 Task”升级为“Control 创建 Task/事件/事务 Outbox，Execution 执行副作用，Worker 幂等回写 Control”。Console 已切换到 Control；任务派发死信具备租户隔离查询和人工重放；V12 以外键级联消除 Task 删除后的 Outbox 孤儿。

验证证据：Control 89/89（真实 PostgreSQL、Flyway V1–V12、无跳过），Execution 133/133（真实隔离 PostgreSQL + TLS MinIO、无跳过），Console 49/49，端点目录 264/264。最终真实入库为 `SUCCEEDED / CONTROL / PUBLISHED / INDEXED`，20 个事件中 19 个明确来自 Execution 回调；清理核对 `KB=0、Task=0、Outbox=0、Event=0、孤儿=0、隔离测试 schema=0`。完整契约见 `docs/design/control-plane-task-ingest-outbox-v1.zh-CN.md`。

Phase 2A 当时的 multipart 缺口已由 Phase 2B 关闭；单文档命令缺口已由 Phase 2C 关闭；批处理与非索引上传分别由 Phase 2D、2E 关闭；旧公开写入口与旧前端退役已由 Phase 2F 关闭。

> 最近复核：2026-08-08。MinIO 文档对象存储、租户容量准入、审批保留事实/趋势、V8 审批告警与可靠 Webhook Outbox、V9 KB 元数据/绑定权威迁移、Task Authority Phase 2D/2E、Workflow 主密钥轮换、三平面 JWT 边界与最终全栈回归已纳入本审计。

## 1. 审计范围与结论

本轮审计覆盖 `apps/`、`services/`、`infra/`、`config/`、`packages/` 与工程文档，采用源码对照、生产构建、单元测试、路由目录契约检查、Compose 解析和本地页面响应式巡检。

总体结论：项目已经具备知识库、Chat、API 治理、系统运维、基础 IAM、Workflow 版本治理、Control Plane Conversation/Run/Event、KB 元数据/绑定以及 Task 运营命令权威状态，并形成 RabbitMQ/Celery Workflow 自动执行闭环。事务 Outbox、执行租约/主动中断、副作用业务幂等、核心 DAG/条件执行、结构化并行 fan-out/join、持久化人工审批、统一节点适配器、租户隔离 Retrieval、安全 HTTP、租户 Secret Vault、节点级重试与 attempt 权威记录、两类死信持久化与 Console 审计重放均已落地。Execution、Control 和 Chat 三个平面均采用显式版本迁移；Provider/HTTP Tool 已通过真实本地网络故障沙箱，商业 Provider 契约已有付费门禁与脱敏报告执行器。文档链路现已真实接入 PostgreSQL + MinIO + Qdrant：对象引用、校验和、发布屏障、删除及移动补偿、逻辑配额、并发容量预留均已验证；文本、三种 multipart 模式、单文档写命令和批处理父子任务已完成 Control Task Authority，公开写入口和统一 Console 也已完成收口。项目达到“可持续开发与本地集成验收”阶段，但尚不能宣称生产完备；主要缺口为第三方 Provider 测试账号实测与账单核对、高级审批协作、跨 Worker/嵌套并行、极端容量故障与压力、多节点部署演练、Vault/KMS、Secret 年龄告警、异地不可变备份与模块拆分。

本文件区分三类状态：

- **已实现并验证**：本轮有构建、测试或自动契约检查证据；
- **已实现未联调**：代码存在且可编译，但依赖服务未启动，未完成运行态验证；
- **未闭环**：关键环节、测试或工程边界仍缺失。

## 2. 系统组成逐项梳理

| 模块 | 当前职责与实现 | 状态 | 主要证据/问题 |
| --- | --- | --- | --- |
| `apps/web-app` | Control Plane 登录、会话侧栏、Chat Plane 流式对话、Markdown 展示、运行状态和引用展示 | 已实现未联调 | 生产构建通过；缺少自动化前端测试与真实流式 E2E |
| `apps/web-admin` | 统一 Console：总览、任务、工作流草稿/校验/版本、审批工作台、运行追踪、运行死信、Workflow Secret、DB/跨存储修复、API 治理、Chat 记忆、KB、租户用户、平台配置 | 已实现并验证 | 55 项测试、生产构建通过；KB 全部公开写操作已切换 Control；跨存储修复支持移动详情与单项重试；multipart 支持多文件、三模式和完成数量反馈 |
| `apps/debug-web` | 三平面接口目录、请求构造、响应查看、DBViz 与治理调试 | 已实现并验证（目录契约） | 287/287 后端路由已纳入，ID 无重复、无悬空路由 |
| `apps/kb-admin-web` | 退役的独立 KB 管理兼容前端 | 已退役（源码保留） | 默认 Compose 不启动；仅 `legacy-kb-admin` profile + 显式兼容写开关可短期回滚，不再承接新功能 |
| `services/control-plane` | Spring Security、标准 JWT/refresh、租户/用户/角色、系统上下文、DBViz、Canvas DSL、Workflow/Version、Conversation/Message/Run/Event/NodeExecution/Attempt/Approval、KB 元数据/绑定与 Task 运营权威 | 核心生命周期、可靠投递、认证轮换、KB 与 Task 分阶段权威迁移及 schema 演进已闭环 | 110 项测试通过；Flyway V1–V13；具备 Run/Task/Approval Outbox、租约、审批恢复、全部 KB 公开写入口，以及入库、单文档和批量父子 Task 的创建/聚合/查询/事件/取消/重试/清理 |
| `services/execution-plane/api` | KB/文档/切片、任务、检索、Agent、API 池、配置中心、日志风控、用量预算、DBViz | 核心 happy path 与关键租户边界已验证 | 登录、临时 KB、文档入库、本地 embedding 和检索通过 Compose smoke；任务查询/事件/取消及 KB 管理路由具备真实 PostgreSQL 跨租户测试；单文件约 6000 行，仍需按领域拆分并扩大服务测试 |
| `services/execution-plane/workers` | Celery 文档流水线、任务取消/重试、跨存储修复、Control Run 消费与 mock/api_strict Workflow Graph Runtime | 条件图、结构化并行、扩展节点与文档索引失败恢复已验证 | 2–8 分支进程内真实并发、隔离状态、确定性 join/失败和单调事件审计已落地；Execution 148 项测试通过；批量 Qdrant collection 写入跨进程串行；API/Worker 使用非 root UID/GID 10001 |
| `services/execution-plane/retrieval` | PostgreSQL 文档/任务/对象元数据、容量预留与修复账本，MinIO 原件/解析文本，Qdrant 向量索引 | 核心不变量、显式迁移、逻辑/物理容量与三存储边界已联调 | `INDEXED` 是检索屏障；V1–V9 迁移具备 checksum、失败留痕、迁移锁和启动门禁；V9 增加批次取消计数/父索引；容量和 Qdrant collection 均使用相应 advisory lock |
| `services/chat-plane` | Control JWT 鉴权、会话/消息/run、流式管线、记忆、检索、Chat API 池与告警 | 核心 happy path、认证边界、schema 演进、流生命周期、Provider 网络故障与熔断状态机已验证 | 39 项测试通过且 0 跳过；OPEN 严格阻断、HALF_OPEN 单探针且恢复时清除旧进程探针声明；本地网络沙箱覆盖流式/非流式 429、401、断连、超时、认证头、审计状态和并发；断线续传及第三方账号实测尚未完成 |
| `infra/compose` | 三后端、三个默认前端、一个 legacy profile 前端及 PostgreSQL/Mongo/Qdrant/MinIO/RabbitMQ 编排 | 基础运行态已验证 | 新镜像成功构建部署；默认服务清单不含旧 KB Admin，历史容器已停止 |
| `packages/*` | SDK、DSL、模板与 solution pack 规划目录 | 未实现 | 当前仅 README/TODO，尚无可复用包或发布链路 |

## 3. 核心业务链路逐项梳理

### 3.1 身份与租户

1. Control Plane 提供注册、登录、刷新、登出、当前用户与管理员租户/用户接口。
2. Web App 使用 Control Plane access token 调用 Chat Plane。
3. Chat Plane 只验证 Control Plane key ring 签发的标准 HS256 JWT，并按会话的 `tenant_id + actor_id` 做所有权检查。
4. Execution Plane 管理接口使用独立 issuer/audience、独立主密钥和角色依赖。

本轮完善：

- Chat 用户接口强制令牌包含 `tenant_id`，不再静默降级为 `platform`；
- Control/Execution 新签发均改为标准三段 JWT，强制 `kid/iss/aud/iat/exp`，未知 `kid` 不回退；
- 两套主密钥在生产门禁中必须独立，previous key 最多 3 个且最长 7 天；
- 两段旧格式默认关闭，只有显式 `LEGACY_ACCEPT_UNTIL` 时临时接受且最长 7 天；
- Chat 只复用 Control 验证 key ring，不再读取 Execution JWT Secret；
- refresh token 改为带随机 `jti` 的单次轮换，事务行锁撤销旧记录并返回新 refresh token；
- 真实 HTTP Smoke 已证明 Control→Chat 正常、三类跨平面调用均为 401、旧 refresh token 重放为 401。

剩余问题：服务到服务调用仍使用静态内部 token；尚需 Vault/KMS、key slot 使用指标、Secret 年龄/轮换逾期告警和集中吊销能力。

### 3.2 知识库与文档入库

已存在创建/重命名/删除/可见性/绑定、上传与文本入库、切片草稿、提交、重索引、总结、标签、批处理、任务批次和 SSE 任务流。

本轮完善：

- 原件与解析文本进入 MinIO，PostgreSQL V4 保存 object key、bucket、SHA-256、大小和类型；
- Worker 使用对象引用，读取上限和 checksum 不满足时 fail closed；multipart 上传改为 `max+1` 有界分块读取，并限制单批文件数与总字节；
- 文档/KB 删除的 Qdrant point/collection 和 MinIO object 清理失败进入持久账本；
- 上传后自动总结改为默认关闭并由 Console 显式勾选，避免无意触发外部模型和延长任务；
- 管理员可租户内下载校验后的原件，Console 可操作修复账本。

未闭环项：文本/三种 multipart 模式、单文档写命令、批处理父子 Task、冻结/草稿元数据写入、旧入口退役、移动专用跨存储修复和隔离单节点磁盘满恢复均已闭环；仍需持续饱和、多节点故障压力和 `raw_text` 重复正文卸载。

### 3.3 Chat、记忆与 Agent Run

已存在会话 CRUD、消息列表、SSE 流式生成、run 查询/取消、Chat 配置、可访问 KB、记忆重排作业、API 池和用量告警。

本轮已补齐 Chat Run 生命周期：主动取消、Provider stream/fallback 双失败、客户端断线、终态不可逆和并发流隔离均有隔离 PostgreSQL 集成测试；失败或取消只保留用户消息，不写入半成品助手消息。当前断线语义为服务端取消，尚不支持 cursor/`Last-Event-ID` 续传。

未闭环项：

- Control Plane 已实现 `conversation/message/run/event` 权威状态机、发布版本快照、内部事件幂等回传、结果/引用/指标持久化和 SSE 历史重放；
- 已实现 Run 与 Outbox 同事务写入、`SKIP LOCKED` 并发领取、指数退避/死信/管理员重放、RabbitMQ/Celery 消费，以及带租约心跳的 Python Runtime 按快照执行回写；
- Runtime 已按 DAG 边执行注册节点，条件分支只执行选中路径，多 LLM 节点传递上一节点答案，并持久化幂等的节点开始/完成事件；
- 引用充足度和工具失败策略仍缺少自动化验收；Run 事件持久化已纳入单元测试与全栈 smoke。

### 3.4 API 池、用量、预算与告警

Execution 与 Chat 均有 API 池管理；Execution 还包含主池/AUX、消费者端点、Key 生命周期、用量、定价版本、预算和成本对账；Chat 包含调用日志、导出、规则、告警、洞察和趋势。

本轮完善：原 Debug Web 漏掉的路由已全部补入，并同步纳入 Workflow、Run、Approval、审批告警、管理员追踪、租约、执行死信、跨存储修复、文档原件下载和 Alertmanager 内部接收路由；目录当前为 243 项，并有可在 CI 中运行的契约脚本。

剩余问题：本地网络沙箱已覆盖限流、认证、超时、断连和并发；真实 Provider 的供应商特定限流头、熔断、账单对账和 webhook 失败重试仍需要测试账号集成。

### 3.5 配置、日志与风险治理

已存在配置 Schema、生效值、草稿校验、影响分析、发布/回滚/默认版本/历史/审计/差异；系统侧存在日志索引、检索、统计、告警预览、权限警告、风险规则/事件、留存和归档。

未闭环项：部分“附属动作”仍采用 best-effort 异常吞掉策略；需要统一结构化错误、指标和补偿任务，避免主请求成功但镜像配置/通知未应用而无可观测信号。

## 4. 前端质量审计

本轮已完成：

- 修复 Console 登录成功反馈回归；
- 修复 390px 移动端登录卡片横向越界和标题裁切；
- 登录区域支持内容超高时滚动，高级配置展开后仍可完整访问；
- 补齐登录表单的浏览器自动填充语义；
- 对 Console 10 个重页面启用异步加载，入口 JS 从约 517 kB 降至约 185 kB，消除单包超 500 kB 警告。
- 为异步页面增加延迟加载提示、自动重试、失败反馈与手动重新加载，并适配 `prefers-reduced-motion`。
- 补齐工作流列表、草稿编辑、结构化校验结果、发布版本审计、归档/删除与乐观锁交互，页面继续使用异步 chunk。
- 修复 390px 顶栏品牌/一级菜单挤压；390px、820px、1280px 工作流页面均无横向溢出，浏览器错误日志为空。

后续精细化重点：

1. 在已完成 16 类交互行、6 类统一分页和主要表格空态播报的基础上，补组件级 DOM 测试；当前没有产品定义的可排序列，如后续引入则统一 `aria-sort` 与服务端排序契约；
2. 将 `MonitorOverview.vue`、`ApiPoolTablesCard.vue` 等超大组件继续按业务卡片拆分；
3. 为 Web App、Debug Web、KB Admin 建立 Vitest + 浏览器 E2E 基线；
4. 完成 Debug Web/KB Admin 向 Console 的迁移或明确长期保留边界，避免双份交互继续漂移。
5. 为跨存储面板增加积压年龄趋势、失败告警和批量治理，而不只依赖人工刷新。

## 5. 后端与工程质量审计

### 已建立的质量门禁

- 四个前端执行锁文件安装、测试（如有）与生产构建；
- Python 两个服务执行 `compileall`；Chat 执行标准库单元/隔离 PostgreSQL 集成测试，并在 CI 中先运行生产迁移入口；
- Control Plane 使用 Java 21 执行 Maven 测试；
- Debug endpoint catalog 与 264 个后端路由做方法/路径/平面一致性检查；
- Compose 文件执行解析校验；
- GitHub Actions 在 PR 和 `main` 推送时运行以上检查。

### 仍需处理的结构风险

| 优先级 | 风险 | 建议收口方式 |
| --- | --- | --- |
| P0 | 本地真实 HTTP Provider/Tool 故障沙箱已覆盖幂等头、429/401、超时、断连、并发和取消，但尚未验证具体第三方供应商差异 | 使用无计费或测试账号复用同一契约，补供应商限流头、计费和流式格式验收 |
| P1 | Tool/Retrieval、Secret、节点重试、attempt、结构化 fan-out/join 和持久化人工节点已落地；仍缺跨 Worker/嵌套并行 | 设计可恢复的分支调度、部分成功与补偿，并保持审批 checkpoint 与 attempt 幂等契约 |
| P1 | 文档三存储、逻辑配额、物理写入准入、版本化生命周期、TLS 有界大对象/并发/版本基线、真实 Alertmanager 接收器、版本化恢复与隔离磁盘满恢复已闭环；生产拓扑极端容量仍未验证 | 补持续饱和、多节点故障、告警提前量和生命周期扫描压力，并按生产拓扑校准阈值 |
| P1 | 巨型源文件导致回归面过大 | 按 domain router/service/repository/schema 分层，保持路由兼容 |
| P1 | 开发配置仍保留显式弱默认值；生产 Secret fail-closed、独立 env、生成器、复用/强度校验、仓库扫描、Workflow 主密钥事务重加密和 JWT 无停机轮换已落地 | 下一步接入 Vault/KMS，补 key slot 使用率、Secret 年龄和轮换逾期告警 |
| P1 | Execution、Control 与 Chat 均已有显式版本迁移，但当前仍共用 PostgreSQL `public` schema；全库逻辑备份与隔离恢复演练已通过 | 发布时按各服务启动门禁分别迁移；继续确定拆库/拆 schema 顺序、最小权限、WAL/PITR 和异地不可变备份 |
| P1 | 旧前端与 Console 功能重复 | 依据 ADR-011 建迁移退役清单，逐页标记 owner 和关闭条件 |
| P2 | `packages/*` 为空壳 | 等 DSL 契约稳定后再提取 SDK/DSL 包，避免提前抽象 |

## 6. 本轮验证记录

| 检查 | 结果 |
| --- | --- |
| `apps/web-admin` Vitest | 53/53 通过（含 Workflow、审批/告警、跨存储账本/健康、KB/Task Control 路由、multipart 三模式/多文件、单文档/批量命令、批次详情重试、边界状态、自定义下拉、Modal 焦点、交互行和分页模型） |
| Chat Plane test | 39/39 通过、0 跳过（含 JWT key ring/旧格式截止、显式迁移、Run/SSE 生命周期、真实 HTTP Provider 故障沙箱与严格熔断状态机） |
| Control Plane Maven test | 109/109 通过且无跳过（含真实 PostgreSQL V1→V13、Hibernate validate、三模式上传意图/激活/完成/超时/持久清理、单文档/批量命令、父子聚合、Task 事件幂等/派发 Outbox/死信重放/删除级联） |
| 四个 Vue 应用生产构建 | 全部通过 |
| Python compileall | 通过 |
| Execution unittest | 150/150 通过且无跳过（真实隔离 PostgreSQL + TLS MinIO；含三模式暂存不可变身份、精确文档描述、批次身份、collection 并发锁、KB 批次/锁/缓存清理、Worker 权威回写与 MinIO 全版本删除） |
| Endpoint catalog | 287/287，100%，无重复/悬空/未登记路由 |
| Document batch authority smoke | 通过：2 个入库、4 个批次、8 个子 Task、102 条 Execution 回调；聚合/Outbox/标签/零 overlap/移动/删除正确，PostgreSQL、Qdrant、MinIO 全版本严格清零 |
| Docker Compose 配置解析 | 通过 |
| Compose 构建与启动 | 13/13 容器运行；三个后端、四个前端均返回 200 |
| Stack smoke | 2026-08-03 复跑通过：Workflow 生命周期、条件双分支选择、`parallel.fork/join`、审批/SLA/保留趋势/告警 Outbox、逐节点事件/attempt、管理员追踪、Retrieval、HTTP 默认拒绝、跨租户保护、Run Outbox、租约与严格清理；随后新增外部 Embedding 默认拒绝门禁并验证预期阻断 |
| Multipart Task Authority smoke | 2026-08-04 通过：真实文件达到 `SUCCEEDED / CONTROL / PUBLISHED / INDEXED`，21 个事件含 19 个 Execution 回调；PostgreSQL、Qdrant、MinIO、孤儿 Outbox、隔离 schema 严格清零 |
| Outbox reliability smoke | 通过：Execution API 停止、重试耗尽、数据库死信、管理员重放、Run 恢复成功 |
| DLQ audit smoke | 通过：RabbitMQ 消费、权威持久化、管理查询、重放、三步审计和严格清理 |
| Cross-store fault smoke | 通过：真实停止 MinIO/Qdrant、修复失败、健康 `CRITICAL`、服务恢复、重试成功、物理资源删除与严格清理 |
| MinIO bucket governance | 通过：一次性初始化器幂等复跑；versioning/ILM enabled；固定规则 ID、90 天和保留 2 个非当前版本经服务端复读一致；隔离 bucket 产生不同 version ID 后严格清零 |
| PostgreSQL backup/restore drill | 2026-08-03 stderr 兼容修复后最终复跑通过：16,263,625 bytes custom archive，SHA-256 `7e306839f89fd4dfde2b8159339658f1331b078b5ba406fdd39f9ee19314acd8`；15,337 个归档对象、70/70 张表、Control/Execution/Chat 迁移账本 8/4/2 一致；另含输出路径越界拒绝、源库不存在的预期失败与严格清理验证，隔离库和容器临时文件均清零 |
| MongoDB backup/restore drill | 通过：`agentpowflow_execution` gzip archive 782,222 bytes；8 个集合、916 条文档逐集合一致；随机 namespace 恢复库和容器临时 archive 均清零 |
| Qdrant snapshot restore drill | 通过：在临时生产鉴权实例中无 Key 请求返回 401，带 Key 的 snapshot create/download/upload 全链路通过；451,072 bytes，2 个 canary point ID/payload 一致，临时容器与 collection 均清零 |
| MinIO object restore drill | 2026-08-08 升级通过：私有 CA 下复用最小权限应用身份；版本化文档 bucket 随机隔离前缀中 3 个逻辑对象、5 个物理版本按顺序归档/恢复并逐版本校验；源/恢复前缀和容器临时文件均清零 |
| Unified backup set | 通过：PostgreSQL、MongoDB、Qdrant、MinIO 4 组件隔离恢复，归档字节数/SHA-256 与各存储专属恢复/清理事实均强制核对；71.319 秒在线时间窗；总 manifest 明确非原子、恢复需静默写入与跨存储对账；恢复库/schema/collection 残留 0，输出越界拒绝 |
| Production Secret gate | 通过：24 个必填配置、14 个独立 Secret；MinIO 明文、三身份复用、遗留共享凭据、Control/Execution JWT 复用、旧 key 复用和过期兼容窗口均预期失败；合规 Compose 多层覆盖解析、仓库扫描 5/5 及全工作树扫描通过，报告不输出值 |
| MinIO identity + TLS | 通过：Application/Governance/Metrics 身份拆分；应用对象 put/get/delete、治理对象访问拒绝、应用管理员访问拒绝；MinIO HTTPS、Execution CA 验证和 Prometheus HTTPS target up；唯一测试键全部版本清零 |
| MinIO bounded pressure | 通过：16 MiB 单对象、16 个 1 MiB 对象/8 workers 并发、同键 10 版本；27 个物理版本复读，0.524 秒完成；清理后前缀版本 0，HTTPS target up、firing 告警 0 |
| MinIO isolated disk full | 通过：128 MiB tmpfs 内先写入 31×4 MiB，再观察 `XMinioStorageFull`；释放后恢复写入/复读成功；临时 bucket/container/helper 零残留，正式 MinIO 未受影响 |
| JWT rotation smoke | 通过：Control/Execution 新令牌均为标准三段 JWT；Control→Chat 成功；Control→Execution、Execution→Control、Execution→Chat 均拒绝；refresh 轮换返回新 token，旧 token 重放拒绝 |
| Workflow master-key rotation | 通过：主/旧 Fernet 双读、新写只用主密钥；默认 dry-run、显式确认、advisory/table lock、全量指纹校验和主密钥复验；11/11 定向测试含真实 PostgreSQL 原子提交、未知密钥整笔回滚、CLI 确认门禁和幂等复跑 |
| 运行态清洁 | 15 个长期服务（含 Prometheus/Alertmanager）running；3 个 MinIO 初始化器成功退出；`apf_it_*` schema、`apf-it-*` bucket、最小权限 smoke 对象版本均为 0 |
| Provider / 文档流水线全栈 E2E | Workflow LLM mock happy path、主动取消、显式失败、两类死信恢复均通过；审计发现当前 `api_strict` 配置会让文档 Smoke 调用真实 Embedding，现已改为默认在创建测试数据前拒绝，只有显式批准开关才允许；尚缺第三方测试账号、SSE cursor 续传和告警触发 |

## 7. 下一阶段建议顺序

### 2026-08-04 Console 交互与无障碍复核补充

- 登录入口已改为语义化表单，支持密码框回车提交，并为忙碌、成功和失败状态提供可读语义。
- 顶部与侧边导航已由伪链接收敛为按钮，补齐主导航、当前页、展开状态、分区和服务连接状态语义。
- 自定义 `UiSelect` 已实现方向键、Home/End、Enter/Space、Escape、Tab、禁用项跳过、焦点恢复、`listbox/option` 状态与唯一控件名称；键盘操作不播放开合动效。
- 增加跳到主要内容入口、全局 `focus-visible`、指针设备悬停门限、按压反馈和 `prefers-reduced-motion` 降级。
- 真实浏览器验收覆盖 1280×800、820×900、390×844；三档页面均无横向溢出，移动端主导航可达，浏览器 Console 为 0 error / 0 warning。
- 自动化结果：Console Vitest 48/48、Vite production build 通过；KB 元数据/绑定、Task 运营命令与文档操作的平面分流具备回归测试。
- 统一 Modal 焦点指令已覆盖总览、平台设置和 API 池共 16 个顶层/嵌套弹窗：提供首焦点、Tab/Shift+Tab 圈定、Escape、背景 `inert`、Body 滚动锁、嵌套栈与关闭后焦点恢复；键盘关闭跳过离场动画。
- 统一交互行指令已覆盖总览日志/风险、API 治理、Chat 记忆、平台版本/审计、API 实例、Run 追踪和死信共 16 类整行操作：行可聚焦、有上下文名称、Enter/Space 激活、`aria-current` 当前态，且不会劫持行内按钮按键。
- 统一 `UiPager` 已覆盖 API 实例、数据库记录、平台调用事件、权限警告、风险事件和风险规则 6 类分页视图；页码/范围/总数通过 live region 播报，边界按钮禁用一致，主要表格空态统一可播报。
- 配置历史的时间表头点击已改为具名原生按钮，明确表达相对/精确显示切换；当前业务表格尚未定义可排序列，不虚构排序语义。

本项已完成首轮基础可访问性、响应式、Modal 焦点、整行选择、分页和空态收口，但不等于完整 WCAG 审计。后续仍需补颜色对比度工具扫描、主要业务页面的组件级 DOM 测试和真实 E2E；若产品引入表格排序，再补统一排序契约。

1. 在已完成 MinIO 物理容量、写入准入、版本化生命周期、最小权限身份、TLS、有界压力基线、隔离磁盘满恢复、真实 Alertmanager 接收器与多版本恢复闭环的基础上，补持续饱和、多节点故障、生命周期扫描压力和告警提前量，并接入正式 PKI、校准生产阈值与外部值班升级渠道。
2. 使用第三方测试账号复用本地 Provider 契约，验证供应商限流头、流式格式、计费和熔断差异。
3. 在已完成的持久化审批、实时 SLA、独立事实趋势和通用可靠 Webhook 基础上补委派/会签/附件/供应商通知与升级策略/长期归档，再扩展嵌套并行、跨 Worker 分支调度、部分成功和补偿策略。
4. 按 domain router/service/repository/schema 拆分 Execution 巨型模块；旧前端已退役并保留受控兼容 profile，待兼容窗口结束后删除源码。
5. 在四类存储隔离恢复、Workflow/JWT 密钥轮换均已实测的基础上，完成统一 backup set、跨存储 checkpoint、Vault/KMS、三平面拆 schema/最小权限、WAL/PITR 与加密异地不可变备份后，再评估生产部署。
