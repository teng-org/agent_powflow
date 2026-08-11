# Powflow Console 施工清单 v1（接口驱动）

> 2026-08-08 复核：知识库创建/变更、文本与三模式上传、单文档/批处理命令、文档/分块冻结及分块草稿均已切换 Control Plane。Execution 公共写接口默认 410，旧 KB Admin 已移出默认 Compose；端点目录当前为 287/287，Console 测试 54/54。

- 版本：v1
- 日期：2026-03-04
- 依据：`docs/decisions/ADR-011-debug-web-full-migration-to-console-ia.zh-CN.md`
- 目标：将 debug-web 全量接口迁移为 Console 可用功能，按“监控面板 + 功能设置”落地（2026-08-02 基线为 182 个）

## 1. 范围与原则
### 1.1 范围
1. 不改造 debug-web 的定位（仍是调试工具）。
2. 新建/完善 Powflow Console 的页面化能力。
3. 对全量接口做页面归属，不允许孤儿接口；接口数量以 `node tools/audit/verify-endpoint-catalog.mjs` 输出为准。

### 1.2 实施原则
1. 先通路后精修：先把功能闭环打通，再做视觉细化。
2. 页面复用优先：统一用 `Table + FilterBar + Drawer + KPI + Trend` 组件族。
3. 接口聚合优先：优先调用后端聚合接口，避免前端拼装高成本统计。

## 2. 里程碑（按迭代）
### M0（壳层）
1. Console 路由与导航框架。
2. 全局登录态与 token 管理。
3. 全局筛选：`tenant/date/task`。

### M1（监控面板）
1. 系统总览页。
2. 任务监控页。
3. 数据库监控页（Execution + Control + Chat Store）。

### M2（功能设置 - 核心运营）
1. 知识库设置页。
2. 对话设置页（会话管理 + Chat 配置）。
3. 租户与用户页。

### M3（API 治理）
1. 主/AUX/Chat API 池统一治理页。
2. 用量与预算页。
3. 告警与日志页。

### M4（开发调试迁移）
1. 全量接口调试页（2026-08-02 基线：182 接口）。
2. 请求示例、导出、手动触发工具箱。

## 3. 页面-接口映射清单（可施工）
## 3.1 监控面板
### A. 系统总览
1. 功能简述：展示平台健康、核心配置、关键异常。
2. 接口：
- `ep-health`
- `chat-health`
- `ep-context`
- `chat-auth-context`
- `sys-runtime-config-get`
- `chat-store-status`
3. 组件：
- KPICard x4
- StatusTimeline
- QuickActions（刷新配置/flush）
4. 验收：
- 首屏 3 秒内完成关键指标加载。
- 异常接口展示可读错误并可重试。

### B. 任务监控
1. 功能简述：统一查看 KB/Chat 任务状态、进度、失败、取消。
2. 接口：
- `task-list-admin`
- `task-stream-admin`
- `task-events-admin`
- `task-cancel-admin`
- `task-clear-admin`
- `chat-run-get`
- `chat-run-cancel`
- `chat-memory-rerank-jobs`
- `chat-memory-rerank-job-detail`
3. 组件：
- TaskTable（状态色：running/success/failed/cancelled）
- EventDrawer（任务详情）
- SSEBadge（流连接状态）
4. 验收：
- 任务状态与事件明细一致。
- 取消后状态不反跳，支持清理历史。

### C. 数据库监控
1. 功能简述：跨平面数据核验、快速排障。
2. 接口：
- `dbviz-overview`
- `dbviz-pg-tables`
- `dbviz-pg-table-rows`
- `dbviz-qdrant-collections`
- `dbviz-qdrant-points`
- `dbviz-qdrant-point-detail`
- `dbviz-mongo-collections`
- `dbviz-mongo-rows`
- `cp-dbviz-overview`
- `cp-dbviz-tables`
- `cp-dbviz-table-rows`
- `chat-store-status`
- `chat-store-flush`
3. 组件：
- SourceTabs（Execution/Control/ChatStore）
- DataTable（分页/列裁剪）
- JsonPreview（按需展开）
4. 验收：
- 大字段默认截断，点开可查看完整值。
- 表格分页稳定，不全量拉取。

## 3.2 功能设置
### D. 知识库设置
1. 功能简述：KB、文档、切片、总结、绑定统一管理。
2. 接口：
- `kb-list` `kb-create` `kb-rename` `kb-delete` `kb-set-visibility`
- `kb-bindings-list` `kb-bindings-upsert` `kb-bindings-delete`
- `kb-summarize`
- `doc-list` `doc-get` `doc-upload` `doc-ingest` `doc-delete`
- `doc-chunks` `doc-chunk-run` `doc-reindex`
- `doc-chunk-draft-put` `doc-chunk-draft-del` `doc-chunk-commit`
- `doc-summarize`
- `task-list-admin` `task-events-admin`
3. 组件：
- KbListPane + DocTable
- ChunkEditorDrawer
- BatchActionModal
4. 验收：
- 文档全链路（上传->切片->提交->总结）可视化可操作。
- KB 绑定租户后，对应租户用户在 chat 可见。

### E. 对话设置
1. 功能简述：会话管理、对话配置、记忆作业管理。
2. 接口：
- `chat-sessions-list` `chat-sessions-create`
- `chat-messages-list`
- `chat-stream`
- `chat-config-get` `chat-config-patch`
- `chat-memory-rerank` `chat-memory-rerank-jobs` `chat-memory-rerank-job-detail`
- `chat-canvas-schema`
- `retrieval-search` `agent-run`（用于联调验证）
3. 组件：
- SessionSidebar + MessagePanel
- ConfigForm（策略开关）
- MemoryJobsTable
4. 验收：
- 会话管理和配置变更可审计、可回显。
- 记忆重排任务状态可追踪。

### F. 租户与用户
1. 功能简述：租户、用户、角色、当前账户资料。
2. 接口：
- `cp-auth-me` `cp-auth-me-patch`
- `cp-admin-tenants-list` `cp-admin-tenants-create`
- `cp-admin-users-list` `cp-admin-users-create` `cp-admin-users-patch`
- `cp-auth-register`（可选入口）
3. 组件：
- TenantTable
- UserTable
- UserProfileDrawer
4. 验收：
- 租户与用户 CRUD 正常。
- 状态/角色变更即时可见。

### G. API 设置与治理
1. 功能简述：主/AUX/Chat API 池治理、预算与告警闭环。
2. 接口：
- 主池：`api-pool-*`
- AUX：`aux-api-pool-*`
- Chat 池：`chat-api-pool-*`
- 用量预算：`usage-summary` `usage-events` `usage-budgets-*` `usage-alerts-*`
- Chat 告警：`chat-api-alert-rules-*` `chat-api-alert-events-*` `chat-api-alert-evaluate`
- 日志导出：`chat-api-call-logs*` `chat-api-export-*`
- 洞察聚合：`chat-api-pool-overview` `chat-api-pool-dashboard` `chat-api-pool-insights` `chat-api-pool-usage-*`
3. 组件：
- EndpointTable（优先级/权重/冻结/默认）
- UsageTrendChart
- AlertCenter
- CallLogTable
4. 验收：
- 可完成端点上下线、默认路由、预算告警确认、日志导出闭环。

## 3.3 开发调试（全量迁移）
### H. 接口调试工作台
1. 功能简述：保留 debug-web 能力并融合到 Console。
2. 接口：全量接口均可从此页调用。
3. 组件：
- EndpointCatalog（按组）
- RequestBuilder
- ResponseViewer
4. 验收：
- 每个 endpoint id 可检索、可发起、可查看响应。

## 4. 共享组件清单
1. `ConsoleLayout`：左导航 + 主容器滚动。
2. `FilterBar`：快速筛选 + 高级筛选抽屉。
3. `DataTablePro`：分页、排序、列控制、导出。
4. `DetailDrawer`：实体详情与编辑。
5. `KPIGrid`：指标卡组合。
6. `TrendPanel`：折线/柱状图。
7. `ActionConfirm`：高风险二次确认。

## 5. 接口缺口（施工前先补）
当前后端路由已全部进入调试目录，可完成首版控制台，不阻塞施工。建议补充但非阻塞：

1. 跨模块聚合总览接口（减少首屏多次请求）。
2. 统一审计检索接口（按 `actor/trace/resource/op` 查询）。
3. 统一枚举接口（状态/角色/任务类型字典），避免前端硬编码。

## 6. 开发任务拆解（可直接指派）
### Sprint-1（5~7 天）
1. Console 壳层与导航。
2. 系统总览 + 任务监控页。
3. DB 监控页。

### Sprint-2（5~7 天）
1. 知识库设置页。
2. 对话设置页。
3. 租户与用户页。

### Sprint-3（5~7 天）
1. API 设置与治理页。
2. 预算告警中心。
3. 导出与日志页。

### Sprint-4（3~5 天）
1. 开发调试页全量迁移。
2. 交互统一与样式打磨。
3. 文档与验收收口。

## 7. 测试与验收清单
1. 功能测试：每页主流程 + 异常流程 + 空状态。
2. 接口覆盖：全量接口均有页面入口或调试入口，并通过自动一致性检查。
3. 权限测试：无权限用户访问拦截准确。
4. 性能测试：首屏、表格分页、趋势图加载时延。
5. 可用性测试：关键操作反馈、错误可恢复。

## 8. 完成定义（Definition of Done）

### 8.1 交互与无障碍基线（2026-08-04）

- [x] 登录使用原生表单与提交按钮，密码框回车可提交，成功/失败/忙碌状态可被辅助技术感知。
- [x] 顶部/侧边导航使用语义按钮，提供主导航、当前页、展开分区和服务状态语义。
- [x] 自定义下拉支持 ArrowUp/ArrowDown、Home/End、Enter/Space、Escape、Tab、禁用项跳过和焦点恢复。
- [x] 自定义下拉使用 `listbox`/`option`、`aria-expanded`、`aria-controls`、`aria-selected`，每个实例具有上下文名称。
- [x] 键盘触发不播放开合动画；悬停仅在精细指针设备启用；支持 `prefers-reduced-motion`。
- [x] 提供“跳到主要内容”和统一 `focus-visible` 样式。
- [x] 1280×800、820×900、390×844 真实浏览器检查无页面级横向溢出。
- [x] Console Vitest 43/43、production build、运行态 Console error/warn 检查通过。
- [x] 16 类可点击数据行支持焦点、上下文名称、Enter/Space、当前态和行内按钮事件隔离。
- [x] 6 类分页视图统一 `UiPager`，提供具名导航、边界禁用与页码/条数 live region；主要表格空态可播报。
- [x] 配置历史时间列的相对/精确显示切换改为具名原生按钮，明确其不是排序表头。
- [ ] 若产品引入表格排序，补统一 `aria-sort`、键盘与服务端排序契约；补分页组件 DOM 测试。
- [x] 统一 Modal 焦点管理：首焦点、Tab 圈定、Escape、背景 inert、Body 滚动锁、嵌套栈和关闭后触发点恢复。
- [ ] axe/颜色对比度自动扫描与 Playwright 主业务流 E2E 门禁。
1. 页面、接口、交互、审计四个维度均通过。
2. 菜单与接口映射表无缺口。
3. 文档更新：ADR + 施工清单 + 使用说明同步。
