# 持久化人工审批实施审计（2026-08-03）

## 结论

人工审批 v1 已完成前后端与真实全栈闭环，可作为下一阶段高级审批和跨 Worker 调度的稳定基线；agent_powflow 整体仍处于“核心链路可验收、生产化治理未收口”状态。

## 本轮完成项

| 层面 | 完成内容 |
| --- | --- |
| DSL | `human.approval` 注册、配置约束、approve/reject 双分支、并行分支禁用 |
| Execution | checkpoint 持久化内容、暂停异常、恢复路由、历史/指标继承、attempt 事件幂等、进度单调 |
| Control | Flyway V6–V8、审批实体/仓储/服务/API、数据库侧 RBAC/租户过滤、SLA 聚合、独立审计事实与 UTC 趋势、乐观锁、幂等、超时、取消、告警事实与 Webhook Outbox |
| Console | 审批列表/详情/筛选、健康/积压/临期/逾期/超时率/处理时长摘要、7/14/30/90 天趋势、告警健康/确认/重放、Run/Node/Event 追踪、二次确认、拒绝原因、并发反馈、响应式布局 |
| 工程 | Compose 容器内端口固定、审批 smoke、接口目录、单元/集成测试和设计记录 |

## 验收证据

- Control 66/66、Execution 90/90、Console 30/30。
- Debug Web 构建通过；接口目录 242/242、覆盖率 100%。
- Stack smoke 输出包含 `durable_human_approval=1 approval_idempotency=1 approval_sla_metrics=1 approval_retained_trend=1 approval_alert_outbox=1 cleanup=1`。
- 冒烟中发现并修复恢复任务把进度重置为 10% 的契约缺陷；现从 checkpoint 继承进度，并由 3 个专门回归用例覆盖。
- 隔离 schema 集成测试发现并修复审批 JSON 的 `@Lob`/PostgreSQL `TEXT` 映射歧义，并证明非平台管理员只能统计和读取命中自身角色的审批。
- 隔离 PostgreSQL 集成测试证明 Run 级联删除实时审批后，独立审计事实、角色过滤趋势和处理时长仍可查询；浏览器验证 1280px/390px 无页面溢出或控制台错误。

## 未完成项

- 生产容量、灾备、真实外部告警接收器、密钥轮换与三平面最小权限仍未收口。
- 审批已有实时 SLA、独立趋势和通用可靠 Webhook，但尚无委派、会签、附件、供应商专用通知/升级策略和法规级长期归档。
- 人工节点暂不进入并行分支；跨 Worker 分支恢复和补偿仍需专门设计。
- Web App、Debug Web、KB Admin 的浏览器 E2E 与旧前端退役仍未完成。
- 巨型 Execution 模块仍需按 domain/router/service/repository/schema 拆分。

因此，本轮“持久化人工审批 v1”可以视为完成，但不能将整个项目标记为完善或生产就绪。
