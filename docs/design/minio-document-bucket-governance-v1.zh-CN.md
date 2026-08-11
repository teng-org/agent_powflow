# MinIO 文档 Bucket 生命周期与写入准入 v1

## 目标

将过去只能在健康接口中发现的“未版本化、无生命周期”状态转为可重复执行、可复读验证、启动失败即阻断的治理动作，同时让物理容量从只读告警升级为生产写入准入。

## 一次性治理初始化器

Compose 服务 `minio-document-bucket-init` 在 Execution API 启动前运行 `python -m runtime.object_storage_governance`。初始化器会：

1. 等待 MinIO 可用并确保显式配置的文档 bucket 存在；
2. 开启 bucket versioning；
3. 读取已有 lifecycle，保留所有非 Agent Powflow 管理规则；
4. 幂等替换固定 ID `agent-powflow-document-retention-v1`；
5. 重新读取服务端状态，严格校验版本化、规则唯一性及实际保留参数；
6. 仅在验证成功后退出 0，Execution API 通过 `service_completed_successfully` 门禁启动。

初始化器不输出访问密钥，也不自动删除当前对象。

## 生命周期语义

管理规则仅作用于 `v1/` 前缀：

- 当前对象不设置自动过期，避免 PostgreSQL 元数据仍有效但 MinIO 原件被后台删除；
- 非当前版本默认 90 天后才可删除；
- 始终保留最近 2 个非当前版本；
- 旧版本清理后允许删除无引用的 delete marker。

配置项：

| 配置 | 默认值 | 边界 |
| --- | ---: | --- |
| `MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS` | 90 | 1–3650 天 |
| `MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS` | 2 | 1–100 个 |

当前 MinIO 不保留 bucket lifecycle 中的 `AbortIncompleteMultipartUpload` 动作，因此本实现不伪造该配置；实际实例通过 MinIO 全局 API 配置清理残留上传，开发栈实测为 `stale_uploads_cleanup_interval=6h`、`stale_uploads_expiry=24h`。生产部署必须单独核对此服务器级配置。

## 物理容量写入准入

`MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=true` 时，每次文档对象写入在逻辑配额预留和 MinIO put 之前读取物理容量快照：

- `OK`、`WARN`：允许继续，WARN 交由告警和运维扩容；
- 低于 critical 水位、节点离线、驱动离线：返回 507，错误码 `OBJECT_STORAGE_PHYSICAL_CAPACITY_CRITICAL`；
- Prometheus 未配置、URL/查询失败、关键指标缺失或 scrape down：返回 503，错误码 `OBJECT_STORAGE_PHYSICAL_CAPACITY_UNAVAILABLE`。

生产配置门禁强制开启该开关；开发环境默认关闭，避免未启用 observability overlay 时阻断本地上传。批量上传的逐文件失败结果同时返回 `status_code`、稳定 `error_code` 和脱敏详情。

## 容器权限

Execution 镜像使用固定 UID/GID `10001:10001`，API、Worker 与治理初始化器均不再以 root 运行。Celery 同时显式启用 `broker_connection_retry_on_startup`，兼容 Celery 6 启动重试语义。

## 验收证据

2026-08-03 真实 Compose MinIO 验证：

- 文档 bucket 从 un-versioned/ILM disabled 变为 versioning enabled/ILM enabled；
- 服务端复读规则 ID、90 天、保留 2 个非当前版本均一致；
- 初始化器连续复跑保持 1 条管理规则；
- 隔离 bucket 连续覆盖同一 key 后产生至少 2 个不同 version ID，并严格清理；
- Execution `126/126` 测试通过，无跳过；
- API/Worker 均以 UID/GID 10001 运行，Celery root 与启动重试警告消失；
- 生产策略门禁覆盖配额、物理写入准入和生命周期参数。

## 剩余边界

- 16 MiB 单对象、16 路并发和 10 版本的有界基线已通过；仍需在生产拓扑执行磁盘满、节点离线、持续饱和和生命周期扫描压力演练；
- API、治理和监控身份已拆分并经真实权限测试；生产 root 凭据仍需迁入外部 Secret Manager/Vault 并建立轮换告警；
- MinIO TLS 与开发 CA 已闭环，生产仍需接入正式 PKI 的签发、续期和吊销流程；
- 多节点纠删码、副本开销和版本化增长必须纳入容量预测，不能只看逻辑租户配额。
