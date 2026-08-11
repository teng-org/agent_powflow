# ADR-010：API 网关与池化治理（高并发/成本/稳定性）

- 状态：Accepted
- 日期：2026-03-03
- 决策者：Agent Powflow 核心开发组
- 关联：`ADR-006`、`ADR-007`、`ADR-008`、`ADR-009`

## 背景
当前系统已进入多模型、多任务、多租户阶段，API 调用存在以下共性风险：

1. 不同业务直接调用第三方接口，缺少统一治理（超时、重试、熔断、限流不一致）。
2. 费用与 token 用量虽有基础统计，但缺乏“按池/按任务/按租户”精细化运营能力。
3. 高并发下缺少成熟选路机制（优先级、权重、健康状态、线路切换策略）。
4. 管理侧缺少完整 API 生命周期能力（冻结/解冻、默认池、模板化配置、质量评估）。

需要引入统一 API Gateway + API Pool 治理层，先保障稳定与成本可控，再逐步升级智能调度。

## 决策
采用“统一网关 + 分池治理 + 可观测闭环”的架构，先支持 OpenAI 兼容接口，其他厂商通过模板扩展接入。

核心原则：

1. **统一入口**：业务层不得直连厂商 API，必须经 `ApiGateway.call(task_type, ...)`。
2. **分池管理**：按任务域拆分池而非单一池。
3. **可靠性优先**：超时、重试、熔断、并发槽位、降级策略必须平台统一实现。
4. **成本可运营**：按天/租户/池/模型/任务类型统计 token 与费用，并支持告警。
5. **租户隔离**：API 资源支持租户作用域 + 平台默认继承，调用侧强鉴权。
6. **可回溯**：所有外部 API 请求都有 request_id/trace_id 与审计日志。

## 范围定义
### 本阶段必须落地（P1，优先）
1. API 资源模型与管理接口：
   - endpoint：`provider/base_url/model/api_key/task_scope/tenant_scope/priority/weight/status/frozen/created_at`
   - pool：`pool_id/task_type/default_endpoint/policy`
2. 统一调用链路：
   - 预检（冻结/租户可用性/并发槽位/熔断状态）
   - 选路（优先级 + 权重 + 健康过滤）
   - 执行（超时 + 重试 + 错误分类）
   - 记账（token/费用/延迟/结果）
3. 稳定性机制：
   - endpoint 并发槽位（硬上限）
   - 熔断（closed/open/half-open）
   - 自动故障转移（failover）
4. 统计与告警：
   - 按天统计：租户/池/模型/任务类别/API endpoint
   - 告警：预算阈值、错误率、可用性、延迟
5. 管理能力：
   - API 冻结/解冻
   - 默认池与默认 endpoint 设置
   - API 优先级/权重可调整
6. Debug-Web 对齐：
   - 新增 API 治理接口测试项
   - 状态面板：可用性、P95、失败次数、熔断状态、费用与用量

### 第二批增强（P2）
1. 动态线路选择：
   - 在优先级/权重基础上加入延迟、可用性、近期错误率评分。
2. 任务级策略模板：
   - `embedding`、`chat_main`、`chat_aux`、`summary` 支持不同重试/超时/限流配置。
3. API 模板库：
   - 仍以 OpenAI 兼容为主，增加可扩展模板元数据（字段校验、默认 headers、能力标签）。
4. 质量评估近期/长期看板：
   - 近期窗口（5min/1h/24h）+ 长期窗口（7d/30d）。

### 预留项（P3，暂不先做）
1. 本地 API 网关（on-prem provider）。
2. 自动价格抓取与自动成本优化。
3. 强化学习式智能选路。

## API 池分层策略
按功能分池（至少）：

1. `embedding_pool`：向量化任务（检索/索引/记忆向量）
2. `chat_main_pool`：主回复生成
3. `chat_aux_pool`：辅助生成（query rewrite、标题、画像、压缩）
4. `summary_pool`：文档/知识库总结

说明：
- 同一厂商可同时出现在多个池中，但策略参数独立。
- 默认先支持 OpenAI 兼容协议，后续通过模板扩展其他厂商。

## 错误处理与熔断标准
1. 错误分类：
   - 不重试：大多数 `4xx`（参数错误、权限错误）
   - 可重试：`429`、`5xx`、网络超时、连接重置
2. 重试策略：
   - 指数退避 + 抖动，最大重试次数按池配置
3. 熔断策略：
   - 连续失败阈值触发 open
   - open 冷却后进入 half-open 探测
   - half-open 成功回到 closed；失败回到 open

## 费用与用量统计模型
最小维度（按天聚合）：

1. `tenant_id`
2. `pool_id`
3. `task_type`
4. `endpoint_id`
5. `model`
6. `date`

指标：
1. `request_count/success_count/fail_count`
2. `input_tokens/output_tokens/total_tokens`
3. `cost_total/cost_input/cost_output`
4. `latency_p50/latency_p95/latency_avg`
5. `timeout_count/retry_count/circuit_open_count`

## 租户与默认策略
1. endpoint 支持作用域：
   - `tenant_scope=global`（平台可共享）
   - `tenant_scope=tenant`（租户私有）
2. 选路优先：
   - 先租户私有 endpoint，再平台共享默认 endpoint。
3. 权限要求：
   - 用户侧只可使用，不可管理；
   - 管理接口需租户管理员或平台管理员。

## 日志与审计
1. API 请求日志必须记录：
   - `request_id/trace_id/tenant_id/actor_id/pool/task/endpoint/model`
   - 请求摘要（人类可读，避免完整敏感 JSON 落盘）
   - 结果状态、耗时、token、费用、错误原因
2. 敏感信息处理：
   - API key 永不明文回显
   - Header/Body 做字段级脱敏

## 数据模型（建议）
1. `api_endpoints`
2. `api_pools`
3. `api_pool_bindings`
4. `api_call_logs`
5. `api_daily_usage_stats`
6. `api_alert_rules`
7. `api_alert_events`
8. `api_health_snapshots`

## 接口规划（最小闭环）
1. 管理接口
   - `POST/GET/PATCH/DELETE /api/v1/admin/api/endpoints`
   - `POST /api/v1/admin/api/endpoints/{id}/freeze|unfreeze`
   - `POST/GET/PATCH /api/v1/admin/api/pools`
   - `POST /api/v1/admin/api/pools/{id}/set-default`
2. 观测接口
   - `GET /api/v1/admin/api/health`
   - `GET /api/v1/admin/api/usage/daily`
   - `GET /api/v1/admin/api/alerts`
3. 运行接口（内部）
   - `ApiGateway.call(task_type, payload, tenant_id, actor_id, trace_id)`

## 验收标准
1. 高并发下请求成功率与延迟稳定，不因单 endpoint 故障导致整体不可用。
2. 单 endpoint 失败可自动 failover，并可观察熔断状态变化。
3. 统计可按“天/租户/池/模型/任务类型”查询并对账。
4. 告警可触发并记录事件（预算、错误率、可用性、延迟）。
5. Debug-Web 能可视化接口状态与统计结果。

## 实施阶段
1. P1（先做）：统一网关、分池、熔断/重试、统计/告警、冻结解冻、管理接口、debug 可视化。
2. P2（增强）：动态选路、任务级模板策略、质量近期/长期看板、多厂商模板。
3. P3（预留）：本地 API、自动成本优化、智能调度。
