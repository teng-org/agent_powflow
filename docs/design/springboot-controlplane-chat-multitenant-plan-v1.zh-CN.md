# Spring Boot 控制平面 + 用户/租户 + 对话系统详细规划（v1）

## 1. 文档目标

本规划用于把现有知识库能力（Python Execution Plane）与未来用户侧对话系统打通，并以 Spring Boot Control Plane 作为权威入口实现：

1. 用户与租户体系（认证、授权、隔离、配额）。
2. 用户侧对话能力（会话、消息、流式回答、引用溯源）。
3. 与现有 KB 任务体系解耦（对话低延迟链路与后台重任务分离）。
4. 可观测性与成本治理（按租户/用户/模型/任务类别统计与告警）。

---

## 2. 总体架构与职责边界

## 2.1 架构分层

1. Presentation 层
- `apps/web-app`：终端用户对话页。
- `apps/web-admin`：租户管理员后台（用户、策略、配额、审计）。
- `apps/kb-admin-web`：平台/知识库维护后台（已实现为主）。

2. Control Plane（Spring Boot，权威状态）
- 用户/租户/IAM（RBAC）。
- 会话与消息权威存储。
- 对话运行状态（Run）与审计。
- 配额、限流、预算、告警。
- 对 Execution Plane 的统一编排调用。

3. Execution Plane（Python）
- 检索、切片、嵌入、总结、重建等 AI 执行能力。
- 异步任务队列（Celery workers）。
- 向量检索与知识库处理。

## 2.2 解耦原则

1. 在线对话链路与后台 KB 重任务分离：
- 对话链路优先低延迟，不依赖重任务 worker 健康。
- 上传/切片/重建/总结仍走 worker 队列。

2. 控制与执行分离：
- Spring Boot 持有权威业务状态与权限策略。
- Python 执行计算与检索，不承载租户治理策略。

3. 租户上下文强约束：
- 所有请求显式携带 `tenant_id`（来自认证声明）。
- 所有读写默认 tenant 过滤，禁止裸查询。

---

## 3. 功能范围（本期）

## 3.1 用户与租户系统

1. 用户与组织
- 租户（Tenant）创建/启停。
- 租户管理员与成员管理。
- 用户状态（启用/禁用/锁定）。

2. 认证授权
- 登录（JWT + Refresh Token）。
- 基于角色的权限（RBAC）。
- 关键操作二次校验（可后续加 MFA）。

3. 权限模型
- 平台管理员：跨租户治理。
- 租户管理员：本租户用户/策略/资源管理。
- 普通用户：对话、查询、个人资源管理。

## 3.2 用户侧对话功能

1. 会话生命周期
- 创建会话、重命名、归档、删除。
- 会话列表分页、按时间/最近活跃排序。

2. 消息能力
- 发送用户消息。
- 接收流式回答（SSE 首期）。
- 保存 assistant 消息与引用证据（citations）。

3. 检索问答
- 按租户可访问范围选择 KB。
- 检索自动排除冻结库/文档/chunk（沿用你已实现逻辑）。
- 支持证据列表回看（doc/chunk/source/score）。

4. 运行控制
- 对话请求取消（前端 stop + 后端 cancel run）。
- 超时回退与错误提示。

## 3.3 成本/配额/告警

1. Token 与费用统计维度
- 按天、租户、用户、模型、任务类别、API key 指纹。

2. 限额策略
- QPS、并发、token/min、cost/day。
- 超限策略：拒绝、降级（低成本模型/缩短上下文）、排队。

3. 告警策略
- 达阈值告警（OPEN/ACK/RESOLVED）。
- 租户维度预算仪表盘。

---

## 4. 数据模型规划（Control Plane / PostgreSQL）

以下为建议核心表（本期最小闭环）：

## 4.1 IAM 与租户

1. `tenants`
- `tenant_id` (pk)
- `tenant_code` (unique)
- `name`
- `status` (`ACTIVE|DISABLED`)
- `plan`
- `limits_json`
- `created_at`, `updated_at`

2. `users`
- `user_id` (pk)
- `tenant_id` (fk, index)
- `email` (unique within tenant)
- `display_name`
- `password_hash`
- `status` (`ACTIVE|LOCKED|DISABLED`)
- `last_login_at`
- `created_at`, `updated_at`

3. `roles`
- `role_id` (pk)
- `tenant_id` (fk)
- `code` (`TENANT_ADMIN|USER|...`)
- `name`

4. `user_roles`
- `id` (pk)
- `tenant_id` (fk, index)
- `user_id` (fk, index)
- `role_id` (fk, index)

## 4.2 对话域

1. `conversations`
- `conversation_id` (pk)
- `tenant_id` (index)
- `user_id` (index)
- `title`
- `status` (`ACTIVE|ARCHIVED|DELETED`)
- `last_message_at`
- `created_at`, `updated_at`

2. `messages`
- `message_id` (pk)
- `tenant_id` (index)
- `conversation_id` (index)
- `run_id` (nullable, index)
- `role` (`USER|ASSISTANT|SYSTEM`)
- `content`
- `citations_json`
- `meta_json`
- `created_at`

3. `chat_runs`
- `run_id` (pk)
- `tenant_id` (index)
- `conversation_id` (index)
- `user_id` (index)
- `status` (`QUEUED|RUNNING|SUCCEEDED|FAILED|CANCELLED`)
- `stage` (`AUTH|RETRIEVAL|RERANK|GENERATION|PERSIST`)
- `model`
- `error_code`, `error_message`
- `prompt_tokens`, `completion_tokens`, `total_tokens`
- `estimated_cost_cny`
- `latency_ms`
- `trace_id`
- `created_at`, `updated_at`

## 4.3 配额与告警

1. `quota_policies`
- `policy_id` (pk)
- `tenant_id` (index)
- `scope_type` (`TENANT|USER|MODEL|TASK_CATEGORY`)
- `scope_value`
- `qps_limit`
- `concurrency_limit`
- `token_per_min_limit`
- `daily_cost_limit_cny`
- `enabled`
- `created_at`, `updated_at`

2. `quota_counters`（可 Redis + 周期落盘）
- `tenant_id`, `scope_key`, `window_start`
- `qps_used`, `concurrency_used`, `tokens_used`, `cost_used_cny`

3. `quota_alerts`
- `alert_id` (pk)
- `tenant_id` (index)
- `scope_type`, `scope_value`
- `metric` (`QPS|CONCURRENCY|TOKEN|COST_CNY`)
- `limit_value`, `actual_value`
- `status` (`OPEN|ACK|RESOLVED`)
- `created_at`, `updated_at`

---

## 5. 接口规划（Control Plane）

## 5.1 认证与用户

1. `POST /api/v1/auth/login`
2. `POST /api/v1/auth/refresh`
3. `POST /api/v1/auth/logout`
4. `GET /api/v1/me`

5. `GET /api/v1/admin/tenants`
6. `POST /api/v1/admin/tenants`
7. `GET /api/v1/admin/tenants/{tenant_id}/users`
8. `POST /api/v1/admin/tenants/{tenant_id}/users`
9. `PATCH /api/v1/admin/tenants/{tenant_id}/users/{user_id}`

## 5.2 对话

1. `POST /api/v1/chat/sessions`
- 入参：`title?`
- 出参：`conversation_id`

2. `GET /api/v1/chat/sessions`
- 分页查询当前用户会话

3. `GET /api/v1/chat/sessions/{conversation_id}/messages`
- 分页消息历史

4. `POST /api/v1/chat/sessions/{conversation_id}/messages`
- 入参：`content`, `kb_id?`, `top_k?`, `model?`
- 行为：创建 run，触发对话编排
- 返回：`run_id`（同步）或 SSE stream id

5. `GET /api/v1/chat/runs/{run_id}/stream`（SSE）
- 事件：`run_started`, `retrieval_done`, `token`, `citation`, `run_done`, `run_error`

6. `POST /api/v1/chat/runs/{run_id}/cancel`

## 5.3 治理与可观测

1. `GET /api/v1/admin/chat/runs`
2. `GET /api/v1/admin/chat/usage/summary`
3. `GET /api/v1/admin/chat/usage/events`
4. `GET /api/v1/admin/chat/quotas`
5. `PUT /api/v1/admin/chat/quotas`
6. `GET /api/v1/admin/chat/alerts`
7. `PATCH /api/v1/admin/chat/alerts/{alert_id}`

---

## 6. 执行链路设计（用户消息 -> 回答）

## 6.1 处理阶段

1. 鉴权与租户上下文注入。
2. 配额检查（并发/QPS/token 预算预扣）。
3. Retrieval（调用 Execution Plane 检索 API）。
4. Rerank（可选，后续开关）。
5. Prompt 组装（系统指令 + 历史摘要 + evidence）。
6. LLM 流式生成（首 token 尽快返回）。
7. 消息与 run 状态持久化。
8. 配额结算与成本入账。

## 6.2 失败与取消语义

1. 用户取消：
- run 标记 `CANCELLING -> CANCELLED`；
- 终止后续 token 推送；
- 释放并发名额，记录取消事件。

2. 外部 API 超时/失败：
- run 标记 `FAILED`；
- 保留阶段和错误码；
- 支持“重试本次 run”。

---

## 7. 与现有 Knowledge Plane 的衔接

1. 继续复用已有接口：
- 检索：`/api/v1/retrieval/search`（或现有 admin/read path 的用户版封装）。
- 现有冻结策略由 Execution Plane 生效（已实现）。

2. 新增调用规范：
- Control Plane -> Execution Plane 必须携带：
  - `X-Tenant-Id`
  - `X-Actor-Id`
  - `X-Trace-Id`
  - `X-Source-Service`

3. 状态权威：
- 用户可见对话状态以 Control Plane 为准；
- Execution Plane 仅返回执行结果与证据数据。

---

## 8. 队列与并发策略

## 8.1 本期建议

1. 对话主链路先不走 Celery（保持流式实时）。
2. 对话并发采用网关/应用内限流 + API 池槽位控制。
3. KB 后台任务继续使用现有 worker 队列，不与 chat 槽位共享。

## 8.2 后续扩展

1. 把可异步子任务（工具调用、长文总结）拆到 `chat_aux_queue`。
2. 对超长对话采用“异步 run + 前端订阅”模式。

---

## 9. 前端规划

## 9.1 用户侧 `web-app`

1. 登录与租户切换（若多租户账号）。
2. 会话列表 + 会话内消息流。
3. 引用证据面板（doc/chunk 跳转）。
4. 停止生成、重试、复制、反馈。

## 9.2 管理侧 `web-admin`

1. 租户、用户、角色、策略配置。
2. 对话运行监控（RUNNING/FAILED/CANCELLED）。
3. token/cost 统计面板与预算告警处理。

---

## 10. 分阶段里程碑与验收

## 阶段 A：基础设施（1-2 周）

1. Spring Boot IAM + tenant 上下文 + JWT。
2. 对话域表（conversations/messages/chat_runs）建模与迁移。
3. Control Plane 到 Execution Plane 调用 SDK（统一 header 注入）。

验收：
- 用户可登录；会话可创建并查询；
- 所有接口 tenant-aware 校验通过。

## 阶段 B：对话 MVP（1-2 周）

1. `send message + SSE stream`。
2. 检索接入 + 引用返回。
3. run 状态机与取消能力。

验收：
- 单租户 50 并发会话稳定；
- 取消成功率 > 99%；
- 平均首 token 延迟达到目标（按环境设阈值）。

## 阶段 C：治理与多租户增强（1-2 周）

1. 配额策略（QPS/并发/token/cost）。
2. 告警与预算。
3. 管理台可视化。

验收：
- 超限策略可生效；
- 可追溯到 tenant/user/model/task_category。

## 阶段 D：生产化（持续）

1. 压测、容灾、灰度、审计强化。
2. 运营能力（报表导出、SLO 告警）。

---

## 11. 风险与对策

1. 对话与 KB 抢资源
- 对策：API 池/并发槽位分离，服务/队列分离。

2. 多租户越权风险
- 对策：全链路 tenant 强制注入 + 数据访问拦截器 + 审计。

3. 成本失控
- 对策：预算阈值、超限降级、模型白名单。

4. 流式链路中断
- 对策：run 状态持久化、断线重连、幂等恢复。

---

## 12. 本期施工清单（可直接拆任务）

1. Spring Boot
- [ ] 建 `tenant/user/role/user_role` 表与实体。
- [ ] 登录鉴权与 RBAC 中间件。
- [ ] 建 `conversation/message/chat_run` 表与 API。
- [ ] SSE 流式输出接口。
- [ ] 配额策略与计数器。

2. Python Execution Plane
- [ ] 提供用户对话检索 API（非 admin 路由）。
- [ ] 统一返回 citation 结构（doc_id/chunk_id/source/score/text_preview）。
- [ ] 继续保持冻结过滤生效。

3. 前端
- [ ] 用户对话页（会话 + 流式输出 + 引用）。
- [ ] 管理页（用户/租户/配额/告警）。

4. DevOps
- [ ] chat/kb 服务拆分部署配置。
- [ ] 观测（trace/log/metrics）统一看板。

---

## 13. 备注

1. 本规划与现有 ADR-002/003/004 保持一致，重点是把“前台对话 + 多租户治理 + 知识库执行平面”落到可施工形态。
2. 字段/接口命名以当前仓库规范为准，实施时可按代码约定微调，但不得破坏 tenant-aware 与边界解耦原则。

