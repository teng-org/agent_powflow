# ADR-014：系统日志与风控增强（System Log + Risk Control Enhancement）

- 状态：Proposed
- 日期：2026-03-10
- 决策者：Agent Powflow 核心开发组
- 关联：`ADR-010`、`ADR-011`、`ADR-012`、`ADR-013`

## 背景
当前系统已具备日志采集、事务日志中心、权限警告预览与基础 API 治理能力，但仍存在关键缺口：

1. 风险分级缺失：接口与动作缺乏统一风险等级定义，难以进行一致告警与策略执行。
2. 敏感动作审计不足：关键管理动作（配置发布/回滚、权限变更、租户边界操作）记录粒度不够，追责链路不完整。
3. 权限警告可用性不足：系统总览中的“权限警告”仅有聚合预览，尚无可展开详情页，定位效率低。
4. 风控策略未闭环：仅有日志记录，尚未形成“识别 -> 评估 -> 决策 -> 处置 -> 回溯”的执行闭环。

## 目标
建设统一的“日志 + 风控”能力层，覆盖监控、告警、审计、处置。

核心目标：

1. 定义可执行的风险分级模型与接口风险规则。
2. 对敏感接口与动作进行结构化审计增强（含前后态、决策结果、责任主体）。
3. 在 Console 完成“权限警告展开页”（P0），支持筛选、排序、钻取与回溯。
4. 将风险等级与告警策略接入现有事务日志中心，实现统一观测。

## 决策

### 1) 风险分级模型（统一口径）
为每条审计事件计算：

1. `risk_level`: `LOW | MEDIUM | HIGH | CRITICAL`
2. `risk_tags`: `auth`、`tenant_boundary`、`config_change`、`data_delete`、`api_governance`、`billing` 等
3. `decision`: `ALLOW | ALERT | CHALLENGE | BLOCK`
4. `reason_code`: 机器可读原因码（如 `AUTH_LOGIN_FAIL_BURST`、`CONFIG_PUBLISH_CONFLICT`）

评分依据（组合）：
4 5
1. 接口敏感度（静态规则）
2. 动作破坏性（写操作、删除、权限变更）
3. 权限等级（平台管理员/租户管理员/普通用户）
4. 行为异常（频率、失败率、跨租户访问尝试）

### 2) 接口风险规则（可配置）
新增运行参数项（平台参数页可编辑）：

1. `security_risk_rules`：接口风险规则集合
2. `security_alert_thresholds`：告警阈值（失败次数、突发频率、时间窗口）
3. `security_sensitive_fields`：敏感字段脱敏规则

规则最小单元：

1. `method`
2. `path_pattern`
3. `action_type`
4. `default_risk_level`
5. `risk_tags`
6. `policy`（`allow/alert/challenge/block`）
7. `enabled`

### 3) 结构化安全审计增强
统一补充以下字段（写入安全日志与数据库审计表）：

1. `event_id`, `trace_id`
2. `actor_id`, `role`, `tenant_id`
3. `method`, `path`, `action`
4. `risk_level`, `risk_tags`, `decision`, `reason_code`
5. `status`, `http_status`
6. `request_fingerprint`
7. `before_json`, `after_json`（按规则脱敏）
8. `metadata_json`（来源页面、客户端版本、请求标识）

### 4) 权限警告展开页（P0）
在 Console 系统总览中，点击权限警告卡片进入详情页，支持：

1. 按 `risk_level`、`category`、`source`、`actor`、`tenant`、`time_range` 筛选
2. 按 `severity + time` 排序
3. 按 `trace_id` 一键追踪关联日志
4. 显示“触发规则 + 原因码 + 建议动作”
5. 支持 `ack/resolved` 状态流转

该页面优先基于现有日志接口聚合；如字段不足，补充后端查询接口。

### 5) 处置策略（分阶段）
Phase A（本阶段）：

1. 以 `ALLOW + ALERT` 为主，不默认阻断业务
2. 对高风险事件产出明确告警并留痕

Phase B（后续）：

1. 对指定接口启用 `CHALLENGE/BLOCK`
2. 接入更严格频控与租户边界策略

## 作用范围与对象
覆盖以下平面与模块：

1. Execution API（管理接口、配置与知识库相关敏感动作）
2. Chat Plane（鉴权、会话管理、敏感管理接口）
3. Console（系统总览、事务日志中心、权限警告详情）

优先接入的高风险接口（首批）：

1. `POST /api/v1/admin/auth/login`
2. `POST /api/v1/admin/config/publish`
3. `POST /api/v1/admin/config/rollback`
4. `POST /api/v1/admin/config/switch-default`
5. `DELETE /api/v1/admin/config/history/{release_id}`
6. 租户绑定/解绑、用户角色变更、批量删除类接口

## 数据与接口规划

### 后端接口增强（计划）
1. `GET /api/v1/admin/system/risk/overview`：风险总览统计（按级别、来源、窗口）
2. `GET /api/v1/admin/system/risk/events`：风险事件列表（支持多条件筛选）
3. `POST /api/v1/admin/system/risk/events/{id}/ack`：告警确认
4. `POST /api/v1/admin/system/risk/events/{id}/resolve`：告警处置完成
5. `GET /api/v1/admin/system/risk/rules` 与 `PATCH /.../rules`：风险规则管理

### 复用现有能力
1. 事务日志中心现有查询能力作为基础数据源
2. 现有 `trace_id` 机制用于日志链路串联
3. 现有配置中心用于风险规则与阈值参数化管理

## 前端信息架构（Console）

### 系统总览增强
1. 权限警告卡片增加“查看详情”入口
2. 进入“权限警告详情页”时自动带入当前时间窗口与默认严重级筛选

### 事务日志中心增强
1. 增加风险筛选维度：`risk_level/risk_tags/decision/reason_code`
2. 列表支持显示 `触发规则`、`处置状态`

## 实施计划

### P0（当前迭代）
1. 定义风险等级模型与接口规则 schema
2. 实现权限警告展开页（筛选 + 排序 + trace 钻取）
3. 关键接口落结构化风险日志（首批高风险接口）

### P1
1. 告警状态流转（ack/resolved）
2. 风险规则可视化配置
3. 异常行为聚合检测（失败突发、跨租户越界）

### P2
1. 部分接口启用挑战/阻断策略
2. 告警通知通道（Webhook/消息中心）

## 风险与缓解
1. 风险：日志量显著增加导致查询性能下降
   缓解：分层存储 + 索引优化 + 窗口查询上限。

2. 风险：规则过严导致误报
   缓解：先告警后阻断，支持规则灰度与回退。

3. 风险：敏感信息暴露
   缓解：统一脱敏策略，禁止原文写入敏感字段。

## 验收标准
1. 系统总览“权限警告”可展开详情页并可用筛选/排序/trace 钻取。
2. 首批高风险接口均带 `risk_level/risk_tags/decision/reason_code` 审计记录。
3. 事务日志中心可按风险维度查询并正确聚合统计。
4. 风险规则与阈值可在平台参数管理中统一配置。

## 非目标（本 ADR 范围外）
1. 一次性覆盖所有接口的阻断策略。
2. 完整 SIEM 外部对接。
3. 企业级多通道告警编排（仅预留接口）。
