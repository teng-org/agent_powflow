# 审批告警与可靠 Webhook 投递 v1

- 状态：已实现并通过真实全栈验收
- 日期：2026-08-03
- 适用范围：Control Plane、Powflow Console、Debug Web、Compose

## 1. 目标与边界

审批告警不能依赖审批请求线程直接调用第三方服务。v1 将“风险事实”和“外部投递”拆开：审批临期、逾期或自动超时时，先在 PostgreSQL 中幂等保存告警，再由独立 Outbox 调度器异步投递。第三方网络故障不会回滚审批事务，也不会丢失待处理投递。

v1 提供通用 JSON Webhook，不绑定 Alertmanager、PagerDuty、飞书或企业微信的供应商格式。供应商适配可在下游接收器完成，或在后续版本增加 channel adapter。

## 2. 状态模型

### 2.1 告警事实 `approval_alerts`

- 类型：`DUE_SOON`、`OVERDUE`、`TIMED_OUT`。
- 严重度：临期为 `WARNING`；逾期和超时为 `CRITICAL`。
- 状态：`OPEN` → `ACKNOWLEDGED`；审批进入终态后，临期/逾期风险转为 `RESOLVED`。
- 幂等键：`approval:<approval_id>:<alert_type>`，数据库唯一约束保证重复扫描只生成一条事实。
- 保留策略：不引用 Run 外键。删除 Run 时，告警与投递记录仍可用于独立审计。

### 2.2 投递 Outbox `approval_alert_deliveries`

- 状态：`DISABLED`、`PENDING`、`IN_FLIGHT`、`RETRY`、`PUBLISHED`、`DEAD_LETTER`。
- 未配置 Webhook URL 时，新投递记录保持 `DISABLED`，不尝试联网。
- 多实例使用 `FOR UPDATE SKIP LOCKED` 并发领取；领取带 `locked_by/locked_until` 租约。
- 每次领取增加 attempt；失败按指数退避重试，超过上限或遇到不可重试错误进入死信。
- Console 仅允许对 `DISABLED` 或 `DEAD_LETTER` 投递执行人工重放；目标仍未配置时拒绝重放。

## 3. 评估与终态联动

定时扫描最多 100 条在临期窗口内的 `PENDING` 审批：

1. `expires_at > now`：创建 `DUE_SOON`。
2. `expires_at <= now`：解决已有 `DUE_SOON`，创建 `OVERDUE`。
3. 审批通过、拒绝或取消：解决开放/已确认的临期和逾期告警。
4. 自动超时：在解决队列风险后另建 `TIMED_OUT` 告警，保留严重事实。

手动 `POST /api/v1/admin/approval-alerts/evaluate` 与定时扫描共用同一事务服务和幂等键。

## 4. API 与 Console

- `GET /api/v1/admin/approval-alerts?status=OPEN|ACKNOWLEDGED|RESOLVED`
- `GET /api/v1/admin/approval-alerts/stats`
- `GET /api/v1/admin/approval-alerts/config`
- `POST /api/v1/admin/approval-alerts/evaluate`
- `POST /api/v1/admin/approval-alerts/{alertId}/acknowledge`
- `POST /api/v1/admin/approval-alerts/{alertId}/replay`

API 按认证租户查询和处置；无效状态 fail closed。Console 展示告警健康、开放/严重、已确认、投递死信、目标主机、attempt 和最近错误，并提供状态筛选、立即评估、确认与重放操作。完整 URL 不进入数据库和公开 DTO。

## 5. Webhook 安全契约

- 默认只允许 HTTPS；开发环境必须显式开启才允许 HTTP。
- 必须配置精确主机白名单，不支持隐式任意外联。
- 拒绝 URI user-info、fragment、无主机地址和不匹配主机。
- DNS 解析后默认拒绝 loopback、link-local、site-local、multicast、unspecified 等私有/特殊地址；本地故障沙箱需显式开启私网访问。
- 禁止自动重定向，降低跨主机重定向绕过风险。
- 请求包含稳定的事件/幂等头；payload 只含告警、租户、审批、Run、节点、角色和时间，不含 checkpoint、审批说明或决定原因。
- URL 只由部署环境变量注入。当前保护基于发起请求前解析；若运行环境面对不可信 DNS，仍应通过 egress proxy、固定 DNS 或网络策略防御 DNS rebinding。

## 6. 部署配置

核心环境变量：

- `CONTROL_APPROVAL_ALERT_EVALUATION_ENABLED`
- `CONTROL_APPROVAL_ALERT_EVALUATION_INTERVAL_MS`
- `CONTROL_APPROVAL_ALERT_DUE_SOON_SECONDS`
- `CONTROL_APPROVAL_ALERT_WEBHOOK_URL`
- `CONTROL_APPROVAL_ALERT_ALLOWED_HOSTS`
- `CONTROL_APPROVAL_ALERT_ALLOW_HTTP`
- `CONTROL_APPROVAL_ALERT_ALLOW_PRIVATE_NETWORK`
- `CONTROL_APPROVAL_ALERT_DELIVERY_ENABLED`
- `CONTROL_APPROVAL_ALERT_MAX_ATTEMPTS`
- `CONTROL_APPROVAL_ALERT_REQUEST_TIMEOUT_MS`

评估和投递使用独立开关，便于迁移、维护和故障隔离。生产建议保持 HTTPS、私网拒绝与精确主机白名单，并通过 Secret 注入 URL。

## 7. 验收记录

- Control Plane 66/66：包含 10 项告警服务/Webhook 测试、5 项真实 PostgreSQL 迁移集成测试；V1→V8、Hibernate validate、Run 删除后告警/投递保留均通过。
- Console 30/30，生产构建通过。
- Endpoint catalog 242/242，覆盖率 100%，无重复、悬空或漏登路由。
- Stack smoke 包含 `approval_alert_outbox=1`，验证临期告警、默认禁用外发、确认持久化和终态消警。
- 浏览器：1280px 无溢出且 4 张指标卡等高；390px 页面宽度 390px、告警区单列、趋势图仅内部滚动；无 warning/error。
- 运行态：13/13 容器运行，公共 schema 已迁移至 V8，隔离测试 schema 为 0。

## 8. 后续范围

通用 Webhook 可靠投递已完成，但以下仍属于后续生产化：供应商专用消息模板与签名、真实外部接收器联调、全链路 TLS/代理证书治理、告警升级与静默策略、法规级长期不可变归档，以及跨区域灾备演练。
