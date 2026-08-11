# docs

最新企业级缺口需求基线：`docs/requirements/enterprise-capability-gap-requirements-v1.zh-CN.md`，对当前仍缺少或部分完成的 16 项企业能力逐项给出功能需求、使用场景例子、功能作用和建议实现，并定义上线优先级与统一完成口径。

最新 Provider 生产化记录：`docs/design/real-provider-contract-gate-v1.zh-CN.md`，定义真实商业账号的显式付费授权、HTTPS/公网、费用上限、脱敏报告和供应商级关闭条件；当前执行器已完成，账号实测仍待授权。

最新跨存储一致性记录：`docs/design/document-move-cross-store-repair-v1.zh-CN.md`，记录 Phase 2G 文档移动的 PostgreSQL 原子迁移、`MOVING` 发布屏障、`MOVE_DOCUMENT_POINTS` 持久修复、单项重试入口、Console 操作和真实 Qdrant 停机恢复证据。

最新写入口收口记录：`docs/design/control-plane-kb-write-retirement-v1.zh-CN.md`，记录 Phase 2F 文档/分块冻结与草稿代理、Execution 公共写 410 门禁、旧 KB Admin legacy profile、统一 Console 和三存储严格清零证据。

最新 Task Authority 实施记录：`docs/design/control-plane-non-index-upload-authority-v1.zh-CN.md`，记录 Phase 2E `UPLOAD_ONLY` / `UPLOAD_AND_CLEAN` 的持久意图、完成事实、崩溃清理、Console 模式选择和 PostgreSQL/Qdrant/MinIO 严格清零证据。

最新 Task Authority 实施记录：`docs/design/control-plane-document-batch-authority-v1.zh-CN.md`，记录 Phase 2D 批次父子 Task、聚合/重试、并发移动修复、Console 切换和 PostgreSQL/Qdrant/MinIO 严格清零证据。

最新 Console 交互复核：`docs/design/console-accessibility-interaction-v1.zh-CN.md`，记录语义化登录/导航、键盘下拉、焦点与响应式实测，并明确后续 WCAG/E2E 边界。

最新实施记录：`docs/audits/durable-human-approval-audit-2026-08-03.zh-CN.md`、`docs/design/durable-human-approval-runtime-v1.zh-CN.md`、`docs/design/approval-retained-audit-trend-v1.zh-CN.md` 与 `docs/design/approval-alert-delivery-outbox-v1.zh-CN.md`，记录人工审批、独立趋势和可靠外部告警的实现、验证证据及剩余生产化边界。

项目文档总入口，集中管理架构、需求、设计、审计与运维资料。

## 分类

- `requirements/`：产品愿景、范围与验收口径。
- `architecture/`：系统边界和总体架构。
- `decisions/`：架构决策记录（ADR）。
- `design/`：模块设计、施工清单与接口映射。
- `audits/`：基于当前代码和验证结果的实现审计。
- `operations/`：启动、调试、故障处理与运行快照。
- `research/`、`meetings/`：研究记录与会议材料。

## 当前审计基线

- `docs/audits/current-implementation-audit-2026-08-02.zh-CN.md`：前后端功能、核心链路、已确认缺口、验证证据和后续优先级（最近复核 2026-08-03）。
- `docs/operations/minio-cross-store-verification-2026-08-03.zh-CN.md`：MinIO/V4/修复 Console 的测试、全栈 smoke 与环境清理证据。
- `docs/design/console-workflow-management-v1.zh-CN.md`：统一 Console 工作流管理、乐观锁、版本审计、生命周期操作与三档响应式验收。
- `docs/design/parallel-fanout-join-runtime-v1.zh-CN.md`：并行 fan-out/join 的 DSL、并发执行、确定性合并、失败/取消和审计契约。
- `docs/design/approval-alert-delivery-outbox-v1.zh-CN.md`：审批风险事实、可靠 Webhook 投递、安全配置、Console 处置与真实全栈验收。
- `docs/operations/postgresql-backup-restore-runbook-v1.zh-CN.md`：PostgreSQL custom archive、隔离恢复、迁移账本/关键表校验与恢复边界。
- `docs/operations/multi-store-backup-restore-drill-v1.zh-CN.md`：四类持久化存储的隔离恢复演练、哈希证据、严格清理及剩余灾备边界。
- `docs/operations/production-secret-management-v1.zh-CN.md`：生产 Secret fail-closed、生成/轮换流程、泄漏扫描和主密钥限制。
- `docs/design/object-storage-capacity-governance-v1.zh-CN.md`：对象存储租户逻辑配额、并发预留、V5/V6 回填、指标与 Console 契约。
- `docs/design/minio-physical-capacity-observability-v1.zh-CN.md`：MinIO 官方指标、Prometheus/Alertmanager、V7 告警事实与恢复闭环。
- `docs/design/minio-document-bucket-governance-v1.zh-CN.md`：MinIO 文档 bucket 版本化、生命周期与生产物理容量写入准入。
- `docs/design/minio-service-identity-tls-v1.zh-CN.md`：MinIO 三类最小权限服务身份、TLS 信任链、生产启动门禁与回归证据。
- `docs/operations/object-storage-capacity-runbook-v1.zh-CN.md`：容量上线检查、逻辑/物理告警处置、配额调整和双 webhook 冒烟。
- `docs/operations/minio-bounded-pressure-smoke-v1.zh-CN.md`：MinIO 有界大对象/并发/版本压力基线、参数上限、严格清理与未覆盖故障场景。
