# ADR-011：Powflow Console 全量接口驱动 IA/UI 规划

- 状态：Accepted
- 日期：2026-03-04
- 决策者：Agent Powflow 核心开发组
- 关联：`ADR-006`、`ADR-007`、`ADR-008`、`ADR-009`、`ADR-010`

## 背景
本 ADR 明确：目标是建设 **Powflow Console（Admin Console）**，不是改造 debug-web 本身。
debug-web 当前承载了完整接口资产，适合做“接口事实源”；Console 需要基于接口事实源反推页面能力与交互结构。

当前接口资产（来源：`apps/debug-web/src/endpoints.js`）：

1. 接口分组：13 组。
2. 接口总量：118 个。
3. 覆盖服务：Execution Plane / Chat Plane / Control Plane。
4. 覆盖能力：系统、KB、文档切片、任务、检索、API池、用量预算、DBViz、对话、记忆、租户 IAM。

## 决策
采用“双向规划法”建设 Console：

1. 正向规划：从接口能力出发，定义页面组件与交互。
2. 反向规划：从控制台业务需求出发，收敛必需接口与缺口接口。
3. 全量纳入：118 个已登记接口全部归位到 Console 菜单体系，避免“有接口无页面”。
4. 子集菜单：一级菜单下使用二级“子集卡片”组织能力，控制信息密度。

## 接口资产清单（全量分组）
| 分组 | 数量 | 主要职责 |
|---|---:|---|
| 系统 | 6 | 健康、上下文、上传配置、runtime-config |
| 管理员鉴权 | 2 | execution 管理员登录/登出 |
| 知识库 | 9 | KB CRUD、可见性、租户绑定、库总结 |
| 文档与切片 | 12 | 入库、上传、切片、草稿、提交、总结 |
| 任务 | 6 | 任务列表/流/取消/事件/清理 |
| 检索与 Agent | 2 | 检索、问答运行 |
| API 池（主） | 6 | 主池端点管理 |
| API 池（AUX） | 6 | 辅助池端点管理 |
| API 用量统计 | 8 | token/费用统计、预算、告警 |
| DB 可视化 API | 8 | PG/Qdrant/Mongo 可视化 |
| Chat Plane | 40 | 会话、流式、chat config、chat api 治理、记忆重排 |
| Control Plane IAM | 10 | 注册登录、租户、用户管理 |
| Control Plane DBViz | 3 | 控制平面 PG 数据浏览 |

## 信息架构（子集菜单）
一级板块固定为两个大区，避免导航过深：

1. 监控面板
2. 功能设置

### 监控面板（二级子集卡片）
1. 系统总览：健康、运行参数摘要、关键异常。
2. 任务监控：KB/Chat 任务流、失败重试、取消态追踪。
3. API 治理监控：可用性、延迟、熔断、预算告警、日志。
4. 对话与记忆监控：会话吞吐、模型调用、记忆重排任务状态。
5. 数据库监控：Execution/Control 的 PG、Qdrant、Mongo、Chat Store。

### 功能设置（二级子集卡片）
1. 知识库设置：KB、文档、切片、冻结、总结、绑定。
2. 对话设置：会话管理、KB挂载、Chat 配置、标题/记忆策略开关。
3. API 设置：主池/AUX/Chat 池配置，默认路由、优先级、权重、冻结。
4. 租户与用户：租户、用户、角色、账户资料。
5. 平台参数：runtime-config、上传限制、系统开关。
6. 开发调试：接口调试、请求示例、手动触发、导出。

## 正向规划（接口 -> 页面与布局）
### A. 系统总览页
功能简述：平台可用性与关键配置入口。
接口预排：

1. `ep-health` / `chat-health`
2. `ep-context` / `chat-auth-context`
3. `sys-runtime-config-get`
4. `chat-store-status`

布局：

1. 顶部 KPI 卡（服务健康、最近错误、任务积压）。
2. 中部趋势卡（按天请求量/错误率）。
3. 底部“快速操作”卡（刷新配置、强制 flush）。

### B. 知识库运营页
功能简述：KB 和文档全生命周期管理。
接口预排：

1. KB：`kb-list` `kb-create` `kb-rename` `kb-delete` `kb-set-visibility` `kb-bindings-*` `kb-summarize`
2. 文档：`doc-list` `doc-get` `doc-upload` `doc-ingest` `doc-delete`
3. 切片：`doc-chunks` `doc-chunk-run` `doc-reindex` `doc-chunk-draft-*` `doc-chunk-commit`
4. 总结：`doc-summarize`
5. 任务联动：`task-list-admin` `task-stream-admin` `task-events-admin`

布局：

1. 左侧 KB 列表 + 右侧文档表格。
2. 文档详情抽屉（切片参数、草稿状态、总结状态）。
3. 页内任务侧栏（仅当前 KB 上下文）。

### C. 对话与记忆页
功能简述：对话运行态与记忆策略治理。
接口预排：

1. 会话：`chat-sessions-list` `chat-sessions-create` `chat-messages-list`
2. 运行：`chat-stream` `chat-run-get` `chat-run-cancel`
3. 配置：`chat-config-get` `chat-config-patch`
4. 记忆：`chat-memory-rerank` `chat-memory-rerank-jobs` `chat-memory-rerank-job-detail`
5. KB检索验证：`retrieval-search` `agent-run`

布局：

1. 左侧会话树，右侧会话详情。
2. 顶部策略卡（窗口、总结、画像、召回开关）。
3. 底部作业监控表（记忆重排 job）。

### D. API 治理页
功能简述：全栈 API 池与预算告警治理。
接口预排：

1. 主池：`api-pool-*`
2. AUX：`aux-api-pool-*`
3. Chat 池：`chat-api-pool-*`
4. 用量预算：`usage-*`
5. Chat 告警：`chat-api-alert-*`
6. 日志与导出：`chat-api-call-logs*` `chat-api-export-*`

布局：

1. 顶部健康与预算总览。
2. 中部分页表（端点、状态、路由参数、熔断参数）。
3. 底部趋势图+告警时间线+日志聚合表。

### E. 租户与用户页
功能简述：多租户身份与授权管理。
接口预排：

1. 认证：`cp-auth-login` `cp-auth-refresh` `cp-auth-me` `cp-auth-me-patch`
2. 租户：`cp-admin-tenants-list` `cp-admin-tenants-create`
3. 用户：`cp-admin-users-list` `cp-admin-users-create` `cp-admin-users-patch`
4. 注册（可选运营入口）：`cp-auth-register`

布局：

1. 左租户、右用户双栏。
2. 用户详情抽屉（状态、角色、资料）。

### F. 数据库可视化页
功能简述：跨平面数据追踪与核验。
接口预排：

1. Execution DBViz：`dbviz-*`
2. Control DBViz：`cp-dbviz-*`
3. Chat Store：`chat-store-status` `chat-store-flush`

布局：

1. 数据源 Tab（Execution / Control / Chat Store）。
2. 表格页 + 行详情抽屉 + 长字段展开。

### G. 开发调试页
功能简述：运维与研发自助诊断。
接口预排：保留 118 个接口“直接调试入口”，与业务页共用授权态。

## 反向规划（功能需求 -> 必需接口）
| 功能需求 | 必需接口子集 | 说明 |
|---|---|---|
| 平台运行监控 | `ep-health` `chat-health` `task-list-admin` `chat-store-status` | 保证首页可观测 |
| KB 运营闭环 | `kb-*` `doc-*` `task-*` | 无任务接口则无法做过程可视化 |
| 对话运维闭环 | `chat-sessions-*` `chat-run-*` `chat-config-*` | 支撑会话管理与故障处理 |
| 记忆能力治理 | `chat-memory-rerank*` + chat config | 支撑策略验证和回溯 |
| API 成本治理 | `usage-*` + `chat-api-pool/usage*` + 告警日志接口 | 支撑预算阈值与告警闭环 |
| 多租户运营 | `cp-admin-tenants-*` `cp-admin-users-*` `kb-bindings-*` | 支撑“人-租户-KB”关联 |
| 数据核验 | `dbviz-*` `cp-dbviz-*` | 支撑跨平面一致性排障 |

## API 预排计划（确保高利用率）
### P0（Console 骨架 + 核心监控）
1. 系统总览、任务监控、基础鉴权。
2. 接口优先级：系统/任务/认证相关（`ep-*` `chat-health` `task-*` `cp-auth-*`）。

### P1（高频运营）
1. 知识库运营、对话与记忆、租户与用户。
2. 接口优先级：`kb-*` `doc-*` `chat-sessions-*` `chat-run-*` `cp-admin-*` `kb-bindings-*`。

### P2（治理能力）
1. API 池治理、用量预算、告警中心、导出。
2. 接口优先级：`api-pool-*` `aux-api-pool-*` `usage-*` `chat-api-pool-*` `chat-api-alert-*`。

### P3（深度排障）
1. DB 可视化、开发调试全量迁移。
2. 接口优先级：`dbviz-*` `cp-dbviz-*` 全部 + 调试页全量 118 接口。

## UI 风格与布局（执行规范）
### 风格
1. 企业控制台浅色基底，强调可扫描性。
2. 统一 token：色彩、间距、圆角、阴影、边框。
3. 状态色严格语义化：成功/警告/失败/冻结/取消。

### 布局
1. 左侧固定导航：一级板块 + 二级子集卡片。
2. 主区三段：Header、ActionBar、Content。
3. Content 标准模板：KPI 卡 + 图表 + 主表 + 详情抽屉。

### 交互
1. URL 保留筛选条件（可分享、可回放）。
2. 表格默认分页，禁止首屏全量加载。
3. 所有高风险操作必须二次确认并写审计日志。
4. 长文本默认截断，hover 和抽屉查看全文。

### Web-Admin 表单布局补充（2026-03-05）
1. 所有输入控件遵循 `box-sizing: border-box`，禁止 `width: 100%` 与 padding 叠加导致溢出。
2. 监控页筛选区统一使用响应式栅格：`repeat(auto-fit, minmax(170px, 1fr))`，避免同一行控件挤压重叠。
3. 筛选区操作按钮在窄屏下占满列宽、宽屏右对齐，保证可点击性和视觉对齐。
4. 页面主内容区域必须设置 `min-width: 0` 与输入控件 `max-width: 100%`，防止日期控件/长占位文本撑破容器。

### Web-Admin 子标题与分块规范补充（2026-03-05）
1. 任一导航域中，侧栏子标题（submenu section）必须和内容区分块标题一一对应，文案保持一致。
2. 每个可跳转子标题对应唯一 `data-menu-section` 锚点，锚点容器内必须存在可见标题，禁止“有锚点无标题”。
3. 分块标题样式统一为：无底色条、加粗大字号（企业后台信息架构标题样式），并在分块间保持固定垂直间距。
4. 单页多个分块同时可见时，侧栏高亮策略按“当前视口最下方可见分块”优先，避免高亮跳动。

### 实施记录（上一版 ADR 至 2026-03-05）
1. 完成 `apps/web-admin` 输入布局修复：筛选区、日期控件、长输入项在窄屏/中屏下不再重叠或撑破容器。
2. 完成导航高亮策略升级：当多个分块同时可见时，侧栏子标题高亮改为“优先高亮视口最下方可见分块”。
3. 完成系统配置页重构：`平台参数` 改为分板块展示，支持板块级编辑弹窗、重置默认、字段提示、长文本放大编辑。
4. 完成运行参数保存协议修复：前端保存 payload 与后端契约对齐（`updates` 结构），修复 `updates cannot be empty` 问题。
5. 完成运行参数 CORS 修复：本地常见前端端口跨域允许策略补齐，`/api/v1/admin/system/runtime-config` 可正常调用。
6. 完成运行参数持久化策略升级：改为“默认配置 + 用户覆盖配置”双文件机制，支持重启后继续恢复/重置。
7. 完成系统总览去冗余：移除重复提示文案与重复按钮，统一“刷新”操作入口。
8. 完成状态去重合并：顶部连接状态移除，侧栏底部保留简洁连接状态（控制平面/执行平面）。
9. 完成系统总览结构升级：由单块改为两块
   - `系统消息`：事务消息、告警信息（前端接口预留）、权限警告（前端接口预留）、进行中任务（支持切换日活跃视图）。
   - `运行统计`：大曲线监控区，支持指标切换（Token/用户增长/新建对话/API费率）与时间粒度切换（7/30/90天）。
10. 完成事务消息弹窗首版：支持按字段/关键字/时间/级别/分类筛选，支持分页，并显示 3 日内 warning/error/critical 统计（info 不计数）。
11. 完成日志生命周期配置字段落地（runtime-config + 系统配置表单）：
   - `system_log_storage_path`
   - `system_log_hot_retention_days`
   - `system_log_archive_retention_days`
   - `system_log_archive_enabled`
   - `system_log_archive_period`
   - `system_log_expired_action`
   - `system_log_path_change_strategy`

### 下一阶段（Phase-Next）：控制接口与策略实现
目标：将当前前端预留能力转为可闭环的后端控制接口与策略执行机制。

#### A. 控制接口实现（P0）
1. 系统日志检索接口
   - `GET /api/v1/admin/system/logs`
   - 支持：`page/page_size/days/level/category/q/from/to`
   - 返回：`items/total`
2. 系统日志统计接口
   - `GET /api/v1/admin/system/logs/stats?days=3`
   - 统计口径：仅 `warn/error/critical`，`info` 不计数
3. 告警预览接口（占位转正式）
   - `GET /api/v1/admin/system/alerts/preview?limit=50`
   - 聚合：用量、计费、并发、存储阈值告警
4. 权限警告接口（占位转正式）
   - `GET /api/v1/admin/system/permission-warnings?limit=50`
   - 记录范围：敏感接口异常/失败访问（正常访问不记录）
5. 运行统计聚合接口
   - `GET /api/v1/admin/system/runtime-stats/daily?days=30`
   - 指标：`tokens/active_users/new_chats/api_cost_total`

#### B. 策略执行实现（P0-P1）
1. 日志保留与归档调度
   - 热存窗口（默认30天）在线可查。
   - 归档窗口（默认60天内）自动压缩归档，按年月分包。
   - 超期数据按策略处理（删除或保留）。
2. 日志路径变更策略
   - 支持 `keep/delete/migrate` 三策略。
   - 路径变更时记录策略审计日志。
3. 策略参数来源统一
   - 仅从 runtime-config 读取策略参数，启动时加载并支持运行中更新。

#### C. 验收口径（P1）
1. 前端系统总览所有卡片可由真实接口驱动，不再依赖空数组兜底。
2. 事务消息弹窗在 3 日、30 日、60 日范围查询与分页正确。
3. 归档文件可下载且命名包含年月，系统页面不直接展示归档内容明细。
4. 日志策略参数在系统配置页可编辑，保存后可在后端策略执行中生效。

## 验收标准
1. 118 个接口全部有归属菜单和页面入口（业务页或调试页）。
2. 监控面板和功能设置两个一级板块完整可用。
3. 核心页均具备筛选、分页、排序、详情抽屉、导出中的至少四项。
4. API 治理页可完成端点配置、预算告警、日志追踪闭环。
5. 租户用户页可完成租户-用户-KB 绑定链路操作。

## 风险与应对
1. 风险：接口命名与页面语义不一致。
   - 应对：以 endpoint id 为唯一键维护“页面映射表”。
2. 风险：功能页重复拉取同类数据。
   - 应对：建立页面级数据编排层，复用 dashboard / grouped 聚合接口。
3. 风险：全量迁移周期过长。
   - 应对：按 P0~P3 交付，每期可独立上线。

## 附录 A：debug-web 全量接口 ID（118）
### 系统（6）
`ep-health`、`ep-context`、`sys-upload-settings-get`、`sys-upload-settings-put`、`sys-runtime-config-get`、`sys-runtime-config-patch`

### 管理员鉴权（2）
`admin-login`、`admin-logout`

### 知识库（9）
`kb-list`、`kb-create`、`kb-rename`、`kb-delete`、`kb-summarize`、`kb-set-visibility`、`kb-bindings-list`、`kb-bindings-upsert`、`kb-bindings-delete`

### 文档与切片（12）
`doc-ingest`、`doc-upload`、`doc-list`、`doc-get`、`doc-chunks`、`doc-chunk-draft-put`、`doc-chunk-draft-del`、`doc-chunk-commit`、`doc-delete`、`doc-reindex`、`doc-chunk-run`、`doc-summarize`

### 任务（6）
`task-list-admin`、`task-clear-admin`、`task-stream-admin`、`task-cancel-admin`、`task-events-admin`、`task-status-public`

### 检索与 Agent（2）
`retrieval-search`、`agent-run`

### API 池（主）（6）
`api-pool-list`、`api-pool-upsert`、`api-pool-update`、`api-pool-set-default`、`api-pool-delete`、`api-pool-reload-default`

### API 池（AUX）（6）
`aux-api-pool-list`、`aux-api-pool-upsert`、`aux-api-pool-update`、`aux-api-pool-set-default`、`aux-api-pool-delete`、`aux-api-pool-reload-default`

### API 用量统计（8）
`usage-summary`、`usage-events`、`usage-budgets-list`、`usage-budgets-upsert`、`usage-budgets-delete`、`usage-budgets-evaluate`、`usage-alerts-list`、`usage-alerts-update`

### DB 可视化 API（8）
`dbviz-overview`、`dbviz-pg-tables`、`dbviz-pg-table-rows`、`dbviz-qdrant-collections`、`dbviz-qdrant-points`、`dbviz-qdrant-point-detail`、`dbviz-mongo-collections`、`dbviz-mongo-rows`

### Chat Plane（40）
`chat-health`、`chat-auth-context`、`chat-sessions-create`、`chat-sessions-list`、`chat-messages-list`、`chat-stream`、`chat-run-get`、`chat-run-cancel`、`chat-canvas-schema`、`chat-config-get`、`chat-config-patch`、`chat-store-status`、`chat-store-flush`、`chat-api-pool-list`、`chat-api-pool-upsert`、`chat-api-pool-freeze`、`chat-api-pool-unfreeze`、`chat-api-pool-set-default`、`chat-api-pool-reload-default`、`chat-api-pool-health`、`chat-api-pool-overview`、`chat-api-pool-dashboard`、`chat-api-pool-insights`、`chat-api-pool-usage-daily`、`chat-api-pool-usage-grouped`、`chat-api-pool-usage-trends`、`chat-api-alert-rules-list`、`chat-api-alert-rules-stats`、`chat-api-alert-rules-upsert`、`chat-api-alert-rules-delete`、`chat-api-alert-events-list`、`chat-api-alert-event-ack`、`chat-api-alert-evaluate`、`chat-api-call-logs`、`chat-api-call-logs-grouped`、`chat-api-export-usage`、`chat-api-export-logs`、`chat-memory-rerank`、`chat-memory-rerank-jobs`、`chat-memory-rerank-job-detail`

### Control Plane IAM（10）
`cp-auth-register`、`cp-auth-login`、`cp-auth-refresh`、`cp-auth-me`、`cp-auth-me-patch`、`cp-admin-tenants-list`、`cp-admin-tenants-create`、`cp-admin-users-list`、`cp-admin-users-create`、`cp-admin-users-patch`

### Control Plane DBViz（3）
`cp-dbviz-overview`、`cp-dbviz-tables`、`cp-dbviz-table-rows`
