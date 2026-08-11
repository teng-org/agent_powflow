# 对象存储容量治理 v1

## 1. 目标与边界

本方案为文档原件和解析文本建立租户级逻辑容量门禁，解决单请求有限流但并发请求仍可能共同越过容量线的问题。容量判断以 PostgreSQL 中已提交对象元数据为权威事实，以短期预留表覆盖“已通过校验、尚未提交对象元数据”的在途上传。

本方案不把逻辑字节数冒充为 MinIO 物理磁盘占用。版本化、删除标记、副本/纠删码、未被 PostgreSQL 引用的孤儿对象和 MinIO 内部开销都可能使物理占用更高；生产环境仍必须接入 MinIO 自身的磁盘、可用空间和节点告警。

## 2. 数据模型与迁移

Execution PostgreSQL 追加两个迁移：

- V5 `object_storage_capacity_reservations`
  - `documents.extracted_object_size_bytes`：解析文本对象的 UTF-8 字节数；
  - `storage_quota_reservations`：`reservation_id`、`tenant_id`、`reserved_bytes`、`expires_at`、`created_at`；
  - `(tenant_id, expires_at)` 索引用于租户统计和过期回收。
- V6 `object_storage_capacity_backfill`
  - 对 V4 已存在且具有解析对象 key、但大小仍为 0 的历史文档，使用 PostgreSQL `octet_length(raw_text)` 回填；
  - 独立追加 V6，避免修改已执行 V5 的名称或 checksum。

已提交逻辑使用量：

```text
used_bytes = SUM(source_object_size_bytes + extracted_object_size_bytes)
```

准入投影：

```text
capacity_delta_bytes = MAX(0, requested_bytes - replaced_document_bytes)
projected_bytes = used_bytes + active_reserved_bytes + capacity_delta_bytes
allowed = projected_bytes <= tenant_quota_bytes
```

## 3. 并发准入协议

1. API 在写 MinIO 前完成解析/清洗，并计算原件和最终解析文本的精确字节数。
2. 配额关闭时不创建预留行；配额启用时进入 PostgreSQL 事务。
3. 事务获取 `pg_advisory_xact_lock(hashtextextended('storage-quota:' + tenant_id, 0))`，同租户容量判断串行化，不同租户互不阻塞。
4. 删除过期预留，读取已提交字节和仍有效的预留字节。
5. 新文档预留完整请求字节；显式覆盖既有 `doc_id` 时只预留正向容量增量。任务去重锁保证同一文档不会有两个活跃覆盖请求。
6. 投影未超过配额时插入正向预留；零增量无需写预留行。超额时不写 MinIO，返回 `507 OBJECT_STORAGE_QUOTA_EXCEEDED`。
7. MinIO 原件、解析文本和 PostgreSQL 对象元数据提交完成后释放预留；任一步失败也释放。
8. 进程崩溃导致未释放时，预留在 TTL 到期后不再计入容量，并由下一次准入事务物理清理；GET 健康检查保持只读。

`UPLOAD_AND_CLEAN` 在持久化前完成清洗，只写一次最终解析文本，避免先写原文、再覆盖清洗文本时出现配额窗口和重复对象版本。

## 4. 请求边界

- multipart 文件继续受单文件设置、`UPLOAD_MAX_FILES_PER_REQUEST` 和 `UPLOAD_MAX_BATCH_BYTES` 约束，并采用 `max + 1` 分块读取，不做无界内存读取；
- JSON 文本入库新增 `INGEST_MAX_TEXT_BYTES`，在创建 KB 和任务去重锁前按 UTF-8 字节拒绝，返回 `413 INGEST_TEXT_TOO_LARGE`；
- 直接文本入库若已获取任务锁后被容量门禁拒绝，会立即释放任务锁；
- multipart 批量接口保持逐文件结果语义，超配额文件进入 `skipped`，包含稳定 `error_code` 和容量详情，其他合规文件可继续处理。

## 5. 配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `OBJECT_STORAGE_TENANT_QUOTA_BYTES` | `0` | 每租户逻辑字节配额；开发环境 0 表示关闭，生产门禁要求正整数 |
| `OBJECT_STORAGE_QUOTA_RESERVATION_TTL_SECONDS` | `300` | 在途预留 TTL，限制为 30–3600 秒 |
| `OBJECT_STORAGE_QUOTA_WARN_RATIO` | `0.8` | 告警水位，限制为 0.1–1.0 |
| `OBJECT_STORAGE_CAPACITY_TENANT_LIMIT` | `100` | 健康响应最多返回的租户数，限制为 1–1000 |
| `INGEST_MAX_TEXT_BYTES` | `10485760` | JSON 文本入库 UTF-8 字节上限，限制为 1 KiB–200 MiB |

生产配置校验器拒绝缺失、非整数或不大于 0 的租户配额。配额变更为更小值时，既有使用量可能立即进入 `CRITICAL`；系统不自动删除数据，只拒绝新写入并要求管理员扩容或清理。

## 6. 可观测性与 Console

`GET /api/v1/admin/system/object-storage/health` 的 `item.capacity` 返回：

- 全局 source/extracted/used/reserved/projected 字节；
- 配额是否启用、单租户配额、告警阈值、最高租户水位；
- 按投影容量排序的租户文档数、各类字节、预留数和最近过期时间；
- `OK | UNCONFIGURED | WARN | CRITICAL` 健康状态和原因。

生产环境未配置配额为 `WARN`，开发环境为 `UNCONFIGURED`。达到告警水位为 `WARN`，达到或超过配额为 `CRITICAL`。

现有 Prometheus 文本端点追加：

- `agent_powflow_object_storage_bytes{kind=...}`；
- `agent_powflow_object_storage_tenant_quota_bytes`；
- `agent_powflow_object_storage_max_tenant_usage_ratio`；
- `agent_powflow_object_storage_over_quota_tenants`；
- `agent_powflow_object_storage_capacity_health{state=...}`。

统一告警预览新增 `object_storage_capacity`；Console 数据库监控页展示已提交、在途预留、单租户配额、最高水位及容量最高的租户列表。

## 7. 已验证不变量

- 两个并发请求各申请 60 bytes、租户配额 100 bytes 时，恰好一个成功，一个被拒绝；
- 超配额在任何 MinIO `put_object` 前拒绝；
- 成功释放幂等，过期预留不计入统计并会在下一次准入前回收；
- 配额关闭不写预留表；
- 文档覆盖只预留正向容量增量，缩小对象无需额外预留；
- 解析对象字节持久化并纳入统计；
- V1–V5 历史 schema 可通过 V6 回填 UTF-8 多字节正文；
- API 健康、Prometheus 和统一告警读取同一容量快照逻辑；
- 完整 Execution 126 项测试在真实 PostgreSQL 与当前 Compose MinIO 下通过，无跳过；
- Console Vitest 与生产构建通过。

## 8. 后续生产化

1. 接入 MinIO 官方 Prometheus 指标，按实际磁盘可用率设置节点/集群级预警和硬阈值。
2. 将全局统一配额演进为租户套餐/例外配置，并提供审计化调额流程。
3. 已建立 16 MiB 单对象、16 路并发和 10 个版本的 TLS 有界基线；继续在专用环境测量更大文件、持续并发、PostgreSQL 租户锁等待和 Worker/API 内存峰值。
4. 对 MinIO 版本化对象、删除标记和跨存储孤儿执行周期盘点，建立“逻辑使用量 vs 物理占用”对账。
5. 配置真实 Alertmanager/接收器，验证容量告警通知、确认、升级和恢复通知。
