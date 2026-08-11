# 文档对象存储 MinIO v1

- 状态：已实现并通过真实容器验证
- 日期：2026-08-03
- 参与方：Execution API、Execution Worker、PostgreSQL、MinIO、Qdrant

## 1. 目标与边界

文档原件与解析文本使用 MinIO 保存，PostgreSQL 保存对象定位信息、SHA-256、大小、内容类型和文档/任务权威状态。Celery 新任务只传对象引用与校验和，不再把整篇解析文本塞入 RabbitMQ。MongoDB 不参与该权威写链路。

当前仍为兼容旧接口在 PostgreSQL `documents.raw_text` 保存文本副本；因此本阶段完成的是“原件/产物进入真实对象存储并从队列卸载大正文”，不是“数据库大字段完全移除”。后续应按文件阈值和回填计划执行 expand/contract。

## 2. 对象命名与隔离

对象 key 不直接暴露租户或知识库原始标识，而使用固定长度 SHA-256 scope：

```text
v1/tenants/{tenant_hash}/kbs/{kb_hash}/documents/{doc_id}/source/{url_encoded_filename}
v1/tenants/{tenant_hash}/kbs/{kb_hash}/documents/{doc_id}/extracted/text.txt
```

- `tenant_hash`、`kb_hash` 为各自标识的 SHA-256 前 32 个十六进制字符；
- 文件名去除路径语义并做 URL 编码，不能通过 `../` 或分隔符逃逸；
- 默认 bucket 为 `agent-powflow-documents`，可用 `MINIO_DOCUMENT_BUCKET` 覆盖；
- 下载 API 先按 tenant/KB 权限查 PostgreSQL 文档，再读取对象，不能只凭 object key 访问。

## 3. 写入、发布与读取不变量

1. API 先写原件对象，再写解析文本对象，分别计算 SHA-256；任一步失败都会补偿删除此前已写对象。
2. PostgreSQL 文档记录与对象元数据成功提交后才创建任务记录并投递 Celery。
3. 新 Celery 载荷包含 `content_object_bucket/key/sha256`，`content` 为空；Worker 仍兼容旧的内联 `content`。
4. Worker 读取对象时强制最大读取大小（默认 64 MiB）并复核 SHA-256；缺失引用、超限、校验失败或 UTF-8 解码失败均 fail closed。
5. multipart 上传以固定块读取到 `limit + 1` 即停止；同时限制每请求文件数和批次总字节，超限文件不进入 MinIO、PostgreSQL 或队列。
6. Qdrant 发布仍以 PostgreSQL `documents.status=INDEXED` 为检索可见屏障；MinIO 对象存在不代表文档已可检索。
7. `GET /api/v1/admin/kbs/{kb_id}/documents/{doc_id}/source` 返回校验后的原件，并携带 `X-Content-SHA256` 和安全的附件文件名。

## 4. 删除与补偿

文档或知识库删除先在同一请求中提交 PostgreSQL 权威删除，再清理 Qdrant 和 MinIO。远端失败不能回滚已提交的 PostgreSQL 删除，而是写入 `cross_store_repairs`：

显式删除按 PostgreSQL 中保存的精确 bucket/key 列举并删除该 key 的全部对象版本和 delete marker，而不是只写一个新的 delete marker。文档跨 KB 移动保留原始对象定位、checksum、内容类型和字节数元数据，因此后续从目标 KB 删除仍能物理清除源前缀对象。

| 存储 | 操作 | payload |
| --- | --- | --- |
| Qdrant | `DELETE_DOCUMENT_POINTS` | 文档作用域来自账本列 |
| Qdrant | `DELETE_COLLECTION` | `collection_name` |
| MinIO | `DELETE_OBJECT` | `bucket`、`key` |

修复执行使用 `FOR UPDATE SKIP LOCKED` 领取 `PENDING/FAILED` 记录，状态经过 `IN_PROGRESS` 到 `SUCCEEDED/FAILED`，尝试次数和最后错误永久留痕。API 删除后会尽力调度 `app.tasks.repair_cross_store`；RabbitMQ 不可用时账本仍是权威恢复入口。

管理入口：

- `GET /api/v1/admin/system/cross-store-repairs`；
- `GET /api/v1/admin/system/cross-store-repairs/stats`：状态计数、最老积压、失败率、分存储统计、阈值和健康等级；
- `GET /api/v1/admin/system/cross-store-repairs/metrics`：供 Prometheus 抓取的文本指标；
- `POST /api/v1/admin/system/cross-store-repairs/execute`；
- `GET /api/v1/admin/system/object-storage/health`：只读检查 bucket、版本化、生命周期和 TLS 治理状态；
- Console“数据库 → 跨存储一致性修复”面板，可筛选、刷新、查看错误和手工执行。

领取后进程崩溃可能留下 `IN_PROGRESS`。执行器会在下一批领取前把超过 `CROSS_STORE_REPAIR_STALE_AFTER_SECONDS` 的记录恢复为 `FAILED` 并重新领取，避免永久卡死。

## 5. 配置与迁移

关键环境变量：

- `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_DOCUMENT_BUCKET`
- `MINIO_SECURE`
- `MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS`
- `MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS`
- `MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES`
- `OBJECT_STORAGE_MAX_READ_BYTES`
- `UPLOAD_MAX_FILES_PER_REQUEST` / `UPLOAD_MAX_BATCH_BYTES`
- `CROSS_STORE_REPAIR_STALE_AFTER_SECONDS`
- `CROSS_STORE_REPAIR_WARN_OPEN_COUNT` / `CROSS_STORE_REPAIR_CRITICAL_OPEN_COUNT`
- `CROSS_STORE_REPAIR_WARN_AGE_SECONDS` / `CROSS_STORE_REPAIR_CRITICAL_AGE_SECONDS`

Execution PostgreSQL V4 为 `documents` 增加 source/extracted 对象元数据，为 `cross_store_repairs` 增加 `payload_json`。MinIO 镜像固定为 `RELEASE.2025-09-07T16-13-09Z`，避免开发栈漂移。

## 6. 验证证据

- Execution 83/83：对象 key、上传/读取/删除、checksum、读取上限、单文件/文件数/批次字节限制、Worker 引用解析、上传中断补偿、删除故障账本、统计与 stale claim 恢复、统一告警/Prometheus、MinIO 治理检查和并行运行时回归；
- 真实 MinIO 容器：上传原件与解析文本、Worker 完成索引、原件字节级下载一致、删除后两对象均为 `NoSuchKey`；
- Stack smoke：JSON 文本入库经 MinIO 引用进入 Worker，检索、Workflow Retrieval 和 Chat 引用闭环通过；
- 环境清理：无 `apf_it_*` schema、无 `apf-it-*` bucket、修复账本无未完成记录、所有 RabbitMQ 队列为 0。

## 7. 后续工作

1. 已完成 bucket 版本化、固定 ID 生命周期、服务端参数复读和真实版本覆盖演练；
2. 已完成 MinIO 官方指标、真实 Alertmanager 双 webhook 与生产物理容量写入准入；
3. 增加大文件/并发上传压力、MinIO 磁盘满和多节点故障测试；
4. 设计 `raw_text` 阈值卸载与历史数据回填，避免数据库长期保存完整重复正文；
5. 生产环境使用外部 Secret 注入、最小权限 access key、TLS、服务端加密和备份恢复演练。
