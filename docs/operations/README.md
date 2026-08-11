# operations

部署、升级、运维手册，以及依赖和 License 说明。

- `docs/operations/object-storage-capacity-runbook-v1.zh-CN.md`：逻辑配额、物理容量、Prometheus/Alertmanager 启停、双 webhook 冒烟与告警处置。

## 当前文档

- `docs/operations/debug-manual-v1.zh-CN.md`：本地联调调试说明书（Docker + 后端 + debug-web）。
- `docs/operations/dev-path-snapshot-2026-02-10.zh-CN.md`：当前开发路径快照（接口/约束/测试回归清单）。
- `docs/operations/postgresql-backup-restore-runbook-v1.zh-CN.md`：PostgreSQL 全量备份、隔离恢复验证、安全清理与正式恢复边界。
- `docs/operations/multi-store-backup-restore-drill-v1.zh-CN.md`：PostgreSQL、MongoDB、Qdrant、MinIO 的非覆盖式恢复演练、实测证据和跨存储恢复顺序。
- `docs/operations/unified-backup-set-drill-v1.zh-CN.md`：四存储统一 backup set、组件归档二次校验、MinIO 多版本恢复语义与非原子时间窗边界。
- `docs/operations/production-secret-management-v1.zh-CN.md`：生产 Secret 生成、启动门禁、仓库扫描、Workflow/JWT 无停机轮换、已有 volume 轮换顺序与当前限制。
- `docs/operations/object-storage-capacity-runbook-v1.zh-CN.md`：生产配额配置、容量健康检查、告警处置、调额原则与 MinIO 物理容量边界。
- `docs/operations/minio-bounded-pressure-smoke-v1.zh-CN.md`：TLS 下的大对象、并发写入、版本膨胀有界基线、安全上限、严格清理及专用压力环境边界。
- `docs/operations/minio-isolated-disk-full-smoke-v1.zh-CN.md`：有界 tmpfs 隔离 MinIO 的真实存储满拒绝、释放后恢复与正式 volume 零影响验证。
- `docs/operations/chat-api-pool-performance-and-probe-2026-08-08.zh-CN.md`：Chat API 池慢响应定位、连接复用与延迟感知路由、精确端点探测、真实端点基线及单企业适用边界。
