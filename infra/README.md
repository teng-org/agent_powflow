# infra

基础设施与运维代码目录，支持开发与部署环境一致性。

## 常用脚本

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack
.\infra\scripts\stop-dev-all.ps1
.\infra\scripts\restart-dev-all.ps1 -BuildStack
```

`start-dev-all.ps1` 会通过 Docker Compose 启动后端、中间件、数据库以及 2 个默认产品前端：

- `web-app` -> `http://localhost:5173`
- `web-admin` -> `http://localhost:5174`

`debug-web` 仅在显式启用 `developer-tools` profile 时启动，不属于产品入口。

旧 `kb-admin-web` 不进入默认栈。只有短期兼容回滚才使用 `legacy-kb-admin` profile，并同时显式设置 `EXECUTION_LEGACY_KB_WRITE_ENABLED=true`；正常知识库管理入口为 `web-admin`。

历史版本使用本机 PowerShell 启动前端并写入 `config/dev-frontends.pids.json`。现在前端已迁入 Docker，启动/停止脚本会默认清理遗留的本机 Vite 进程，避免端口占用。

## 栈级脚本

```powershell
.\infra\scripts\start-stack.ps1 -Build
.\infra\scripts\start-stack.ps1 -Build -Observability
.\infra\scripts\start-stack.ps1 -Build -Observability -MinioTls
.\infra\scripts\start-stack.ps1 -Build -Observability -MinioTls -EdgeGateway
.\infra\scripts\stop-stack.ps1
.\infra\scripts\stop-stack.ps1 -Observability
.\infra\scripts\stop-stack.ps1 -Observability -MinioTls
.\infra\scripts\stop-stack.ps1 -WithVolumes
```

`start-stack.ps1` 会根据 `config/service-ports.json` 和 runtime config 生成 `config/stack.generated.env`，再启动 `infra/compose/docker-compose.stack.yml`。

`-Observability` 追加 `docker-compose.observability.yml`，使用 MinIO 动态 Prometheus Bearer Token、Prometheus 和 Alertmanager。两个监控 UI 只绑定 `127.0.0.1:9090/9093`；启动后执行 `tools/smoke/minio-observability-smoke.ps1` 验收抓取、规则和告警接收。停止时需同样传入 `-Observability`；生产栈还需传入 `-Production`，以纳入边缘网关和生产覆盖。只有明确需要删除监控时序数据时才同时使用 `-WithVolumes`。

`-MinioTls` 追加 `docker-compose.minio-tls.yml`。开发环境缺少证书时会在 `.local/minio-certs` 生成本地 CA；生产环境拒绝自动生成，必须预置 `server.crt`、`server.key`、`ca.crt`。停止/重启时必须传入同一开关。执行 `tools/smoke/minio-least-privilege-smoke.ps1` 验证应用对象读写、治理身份对象拒绝和精确清理。

`-EdgeGateway` 启用 DB-less Kong，公开 `PUBLIC_API_PORT`（默认 `8088`）并只代理 `/api/v1/deployments/*/invoke`。Kong 负责入口级请求体限制、IP 粗限流和请求 ID；Deployment API Key、租户资产授权和每个发布的精细限流仍由 Control Plane 判定。生产模式自动启用该 profile，并通过 Compose 覆盖移除 PostgreSQL、MongoDB、RabbitMQ、MinIO、Qdrant、Control、Execution、Chat 的宿主机端口，只保留两个控制台与公开 API 入口。Kong 的 Docker DNS 缓存覆盖为 1 秒且不使用过期地址，Control 容器滚动重建后无需人工重启网关。

在 TLS + observability 栈上可执行 `tools/smoke/minio-bounded-pressure-smoke.ps1`，建立单对象、并发对象和版本膨胀的有界基线。默认约写入 33 MiB、硬上限 192 MiB，并只清理本次随机唯一前缀；它不替代磁盘满、持续饱和或多节点故障演练。

Execution API 启动前会运行一次性 `minio-document-bucket-init`：开启文档 bucket 版本化，并幂等校验固定 ID 生命周期规则。生产环境还应使用 `-Observability` 并设置 `MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=true`；否则生产配置门禁会拒绝该配置。

## 运行态 Smoke

完整栈启动后执行：

```powershell
.\tools\smoke\stack-smoke.ps1
```

该脚本验证三个后端健康、双平面登录、Chat token/租户边界、Canvas DSL，以及临时 KB/Chat 会话 CRUD；不调用真实模型，测试数据会自动清理。详细说明见 `tools/smoke/README.md`。

## PostgreSQL 备份恢复演练

完整栈启动后，可生成 custom-format 全量备份，并自动恢复到随机隔离数据库，校验表结构、迁移账本和关键业务表：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-postgres-backup-restore.ps1
```

成功后保留备份和 SHA-256 manifest；隔离数据库与容器临时文件会严格清理。操作边界和正式恢复要求见 `docs/operations/postgresql-backup-restore-runbook-v1.zh-CN.md`。

MongoDB、Qdrant 与 MinIO 的非覆盖式恢复演练：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-mongodb-backup-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-qdrant-snapshot-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-minio-backup-restore.ps1
```

统一恢复顺序、实测哈希和限制见 `docs/operations/multi-store-backup-restore-drill-v1.zh-CN.md`。

## 生产 Secret 门禁

首次生产部署先生成独立、未跟踪的 Secret 文件：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\new-production-env.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\start-stack.ps1 -Production -Observability -MinioTls -ProductionEnvFile .env.production -GenerateOnly
```

确认后去掉 `-GenerateOnly` 并按需添加 `-Build`。生产模式强制 `-Observability -MinioTls`，并拒绝缺失、过短、占位符、格式错误、跨用途复用的 Secret、明文 MinIO 或复用服务身份。已有 volume 的凭据不能仅通过修改 env 轮换，详见 `docs/operations/production-secret-management-v1.zh-CN.md`。
