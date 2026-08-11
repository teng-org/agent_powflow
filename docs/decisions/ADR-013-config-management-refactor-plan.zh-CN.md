# ADR-013：配置管理改造计划（Config Management Refactor）

- 状态：Proposed
- 日期：2026-03-06
- 决策者：Agent Powflow 核心开发组
- 关联：`ADR-010`、`ADR-011`、`ADR-012`

## 背景
随着 Console、KB、Chat、API 治理与日志中心能力持续扩展，当前配置与参数管理出现了明显复杂化：

1. 配置来源多头并存（default/user/legacy 文件、接口 patch、前端默认常量、池配置文件）。
2. 作用域不清（平台级、租户级、用户级、会话级混用）。
3. 缺少统一版本治理（草稿、发布、回滚、变更 diff、审计追溯）。
4. 秘钥与普通参数混放，存在误暴露风险。
5. 各服务热更新与生效机制不一致，易出现“改了未生效/显示不一致”。

该问题已影响配置可维护性、可审计性和后续租户化演进。

## 目标
建设统一的配置控制面，形成可扩展、可审计、可回滚的配置治理体系。

核心目标：

1. 单一事实源（Single Source of Truth）用于“有效配置”读取。
2. 统一生命周期：`draft -> validate -> publish -> notify -> observe -> rollback`。
3. 明确配置作用域：`platform / tenant / user`。
4. Secret 与非 Secret 分离管理。
5. 服务侧一致生效（事件通知 + 缓存失效）。
6. 全链路审计（谁在何时改了什么、影响范围与结果）。

## 现状盘点（As-Is）

### Execution Plane
1. 运行配置三层加载：`runtime-config.default.json` + `runtime-config.user.json` + 兼容 `runtime-config.json`。
2. 管理接口：`GET/PATCH /api/v1/admin/system/runtime-config`（白名单键）。
3. API 池独立默认文件：`api_pool.default.json` / `aux_api_pool.default.json`。

### Chat Plane
1. 读取 runtime 配置（`RUNTIME_CONFIG_PATH`）。
2. 具有独立 `chat_api_pool.default.json` 与状态持久化机制。

### Control Plane
1. 以 Spring 配置 + 环境变量为主。
2. 与 execution admin 鉴权参数存在耦合映射。

### 前端控制台
1. 通过 execution runtime-config 接口保存平台参数。
2. 部分配置在前端存在默认常量与本地状态兜底。
3. 视图类配置已开始从 localStorage 向 admin 持久迁移。

## 问题归因
1. 写入口分散，读取口径不统一。
2. 参数语义分层未建立（策略、路由、偏好混杂）。
3. 发布行为缺失，变更不可回放。
4. 部分敏感项仍可能通过非受控路径暴露。

## 决策（To-Be）
采用“统一配置控制面 + 分层作用域 + 发布制”的改造策略。

### 1) 统一配置控制面（推荐 Postgres JSONB）
新增核心模型：

1. `config_schema`
2. `config_values`
3. `config_releases`
4. `config_audit_logs`
5. `config_subscriptions`（可选）

### 2) 作用域与覆盖规则
覆盖优先级：

`user > tenant > platform > schema_default`

说明：

1. 平台级用于系统基线策略（如预算阈值、日志保留策略）。
2. 租户级用于租户业务策略覆盖（如 API 路由默认、KB 策略）。
3. 用户级用于偏好项（如控制台筛选视图、展示习惯）。

### 3) 配置类型分层
1. **核心运行参数**：强一致、发布制、审计强约束（建议主存 Postgres）。
2. **策略路由参数**：与 API 池治理联动，按租户可覆盖。
3. **用户偏好参数**：可高频变更，允许轻量存储与快速扩展。

### 4) Secret 管理
1. Secret 不以明文存储于普通配置值。
2. 配置层只存 `secret_ref`，真实密钥存 KMS/Vault（过渡期可 DB 加密列）。

### 5) 变更传播
1. 发布成功后发 `config.changed` 事件。
2. Execution/Chat/Control 订阅并刷新本地缓存。
3. 保留手动 reload 兜底接口。

## API 规划（管理面）
1. `GET /api/v1/admin/config/schema`
2. `GET /api/v1/admin/config/effective?scope_type&scope_id`
3. `PATCH /api/v1/admin/config/draft`
4. `POST /api/v1/admin/config/publish`
5. `POST /api/v1/admin/config/rollback`
6. `GET /api/v1/admin/config/history`
7. `GET /api/v1/admin/config/diff?from&to`

兼容策略：

现有 `PATCH /api/v1/admin/system/runtime-config` 保留为过渡接口，内部转写配置中心。

## 迁移计划

### Phase 0（止血）
1. 前端移除/屏蔽真实密钥默认值展示。
2. 增加“写后读校验”，防止 UI 与实际配置不一致。
3. 明确 runtime-config 仅作为过渡入口，不再新增业务新键。

### Phase 1（配置中心落地）
1. 建立配置中心数据模型与管理 API。
2. 迁移高频关键项：
   - `system_log_*`
   - `api_*_threshold`
   - `chat_*` 核心策略项
3. 保留旧接口兼容层。

### Phase 2（发布制与通知）
1. 引入 `draft/publish/rollback`。
2. 变更事件通知与服务侧缓存刷新。
3. 配置审计和差异视图上线。

### Phase 3（分层完善）
1. 租户级覆盖与授权。
2. 用户偏好配置域独立（将日志视图等从平台运行参数迁出）。
3. Secret 外部化。

## 对现有“视图配置”的定位
当前 `system_log_saved_views` 为过渡实现，属于“用户偏好类配置”，长期不应放在平台运行配置域。

后续目标：
1. 迁移至用户配置域（`scope_type=user`）。
2. 支持跨设备同步（同账号）和权限隔离。
3. 与平台参数解耦。

## 风险与缓解
1. **风险：迁移期配置双写导致不一致**
   - 缓解：写路径单向代理，旧接口只读或内部转写。

2. **风险：服务热更新窗口期行为差异**
   - 缓解：配置版本号 + 事件重放 + 手动 reload。

3. **风险：密钥迁移期间泄漏**
   - 缓解：前端永不回显密钥全文，后端只返回掩码与引用。

## 验收标准
1. 所有可配项可在控制台查看“有效值来源”（platform/tenant/user/default）。
2. 任意配置变更可追溯操作人、变更内容、发布时间、回滚记录。
3. 发布后各服务在可接受窗口内（目标 < 30s）一致生效。
4. 无新增明文密钥前端暴露路径。
5. 日志/视图等偏好配置从运行参数域中剥离完成（阶段验收）。

## 非目标（本 ADR 范围外）
1. 一次性替换所有现存配置接口。
2. 立即引入完整外部 Secret 平台（可分阶段实施）。
3. 改造业务实体参数本身（仅改造“配置管理机制”）。
