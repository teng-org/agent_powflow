# 对象存储容量运维手册 v1

## 上线前检查

1. 在 `.env.production` 设置正整数 `OBJECT_STORAGE_TENANT_QUOTA_BYTES`，并根据业务文件规模设置文本和批次上限。
2. 执行生产配置门禁：

```powershell
.\infra\scripts\validate-production-secrets.ps1 -EnvFile .env.production
```

3. 先执行 Execution 迁移，再启动 API/Worker；日志应显示 `current=8` 或 `version=8`。
4. 使用平台管理员令牌检查 `/api/v1/admin/system/object-storage/health`，确认 `capacity.health=OK`，或明确处理 `WARN/CRITICAL` 原因。
5. 抓取 `/api/v1/admin/system/cross-store-repairs/metrics`，确认容量指标已进入 Prometheus。

## 告警处置

### WARN：租户达到预警水位

- 读取 `capacity.items`，定位最高水位租户；
- 核对源文件、解析文本和在途预留的构成；
- 判断是正常增长、异常批量入库还是预留尚未过期；
- 在调高配额前确认 MinIO 实际可用磁盘足够，并记录调额原因。

### CRITICAL：达到/超过配额

- 新上传会安全拒绝，不应反复重试同一请求；
- 若由降低配置导致历史数据超额，恢复合理配额或清理明确可删除的数据；
- 不直接删除 MinIO object，文档删除必须走业务 API，以便 PostgreSQL/Qdrant/MinIO 补偿账本保持一致；
- 检查 `storage_quota_reservations` 的最近过期时间。有效预留属于在途请求；过期行不计入统计，并由下一次容量准入事务自动清理。

### 容量检查失败

- 健康接口会返回 `capacity.health=CRITICAL`，但不会泄露数据库连接信息；
- 检查 PostgreSQL 可达性和 Execution V7 迁移状态；
- 修复数据库后重新读取健康接口，不需要手工删除预留。

## 配额调整原则

- 上调：先确认物理容量，再修改环境变量并滚动重启 Execution API；
- 下调：先读取最高水位，避免把正常租户立即置为 `CRITICAL`；
- 关闭：只允许开发/临时诊断使用 `0`，生产校验器会拒绝；
- TTL：应大于正常的两次 MinIO 写入和 PostgreSQL 提交耗时，不得用极短 TTL 规避锁等待。

## 物理容量监控

使用可观测性叠加启动栈：

```powershell
.\infra\scripts\start-stack.ps1 -Observability
```

Prometheus 与 Alertmanager 默认仅监听 `127.0.0.1:9090` 和 `127.0.0.1:9093`。上线检查应确认 MinIO target 为 `up`、5 条容量/节点/驱动规则已加载、Execution 健康接口的 `physical_capacity` 非 `UNCONFIGURED`，并执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\smoke\minio-observability-smoke.ps1
```

冒烟只有在合成告警与恢复通知都成功投递后才通过。告警事实由 Execution V7 持久化，当前 firing 事实进入统一告警预览，resolved 事实默认保留 90 天。详细架构见 `docs/design/minio-physical-capacity-observability-v1.zh-CN.md`。

## Bucket 生命周期与写入准入

生产配置必须明确设置：

```text
MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS=90
MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS=2
MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=true
```

启动日志必须出现 `MINIO_DOCUMENT_GOVERNANCE_OK`，随后 Execution API/Worker 才能启动。健康接口应显示 `versioning_status=ENABLED`、`managed_lifecycle_rule_count=1`，且实际保留参数与环境变量一致。

物理写入准入的处置语义：

- `OBJECT_STORAGE_PHYSICAL_CAPACITY_CRITICAL` / 507：先停止批量入库，检查可用比例、离线节点和离线驱动；扩容或恢复冗余后再重试；
- `OBJECT_STORAGE_PHYSICAL_CAPACITY_UNAVAILABLE` / 503：检查 Prometheus target、查询 API、凭据文件与 observability overlay，不应绕过门禁直接关闭开关；
- WARN 不阻断写入，但必须评估版本化增长和扩容时间。

当前对象不会被 lifecycle 自动删除；业务删除会产生版本历史，旧版本满足“到期天数且超出保留数量”后才由 MinIO 清理。还需核对 MinIO server 的 `stale_uploads_cleanup_interval` 与 `stale_uploads_expiry`，它们不属于 bucket lifecycle。完整设计见 `docs/design/minio-document-bucket-governance-v1.zh-CN.md`。

## 有界压力基线

在 TLS 与 observability overlay 均已启用后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\smoke\minio-bounded-pressure-smoke.ps1
```

脚本默认写入约 33 MiB，覆盖 16 MiB 单对象、16 路并发对象和同键 10 个版本；总写入硬限制为 192 MiB。所有对象只位于本次随机唯一前缀，并在 `finally` 中删除全部版本并复查为 0。参数、2026-08-04 基线和不覆盖场景见 `docs/operations/minio-bounded-pressure-smoke-v1.zh-CN.md`。

该结果证明有界 TLS 对象链路和版本清理；隔离单节点磁盘满拒绝与释放恢复另由 `tools/smoke/minio-disk-full-smoke.ps1` 验证。两者仍不等同于持续饱和、多节点或业务 API 端到端压力验收。

## 当前限制

逻辑容量不含 MinIO 历史版本、删除标记、纠删码/副本开销和孤儿对象。开发栈已接入官方物理容量、节点和驱动指标，并完成有界大对象/并发/版本压力基线及隔离单节点存储满恢复；多节点网络分区、驱动离线、持续饱和、生命周期扫描和告警提前量仍需在生产拓扑的独立故障演练中验证。
