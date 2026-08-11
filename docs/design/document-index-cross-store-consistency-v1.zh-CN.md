# 文档索引跨存储一致性 v1

## 1. 范围与结论

当前文档入库涉及三个真实存储：PostgreSQL 保存文档、分块、向量副本、任务、对象元数据和发布状态；MinIO 保存原件与解析文本；Qdrant 保存可检索向量。PostgreSQL 是权威状态与补偿账本，MinIO/Qdrant 是可重试的远端参与方。MongoDB 用于日志/可视化等外围能力，不参与文档权威写链路。

系统不尝试伪造跨 PostgreSQL、MinIO、Qdrant 的分布式事务，而采用“发布屏障 + 幂等远端操作 + 持久修复账本”。只有 PostgreSQL 中 `status=INDEXED` 的文档能从检索接口返回。

## 2. 写入与发布不变量

1. 上传先写 MinIO 原件和解析文本，再提交 PostgreSQL 文档与对象定位元数据；失败时删除已写对象。
2. PostgreSQL 提交完成后才创建/投递任务；新任务只携带对象引用和 SHA-256，Worker 读取时复核大小与校验和。
3. 分块预览只写 PostgreSQL，不提前删除 Qdrant 上一代索引。
4. EMBEDDING 阶段逐块持久化 PostgreSQL 向量，不逐块发布 Qdrant。
5. INDEXING 阶段从 PostgreSQL 读取整篇当前分块，校验所有向量非空且维度一致。
6. 文档级发布先删除该文档在 Qdrant 的旧点，再批量写入完整当前代；重复执行结果一致。
7. 只有 Qdrant 发布成功且计数完整后，文档才进入 `INDEXED`。
8. 检索结果必须同时满足：KB 存在且未冻结、文档存在且为 `INDEXED`、文档未冻结、分块仍存在且未冻结。

## 3. 故障矩阵

| 故障点 | PostgreSQL 状态 | 远端可能状态 | 对外可见性 | 恢复方式 |
| --- | --- | --- | --- | --- |
| MinIO 第一个/第二个对象写失败 | 无文档记录 | 无对象或局部对象 | 不可见 | 同请求补偿删除；补偿失败进入账本 |
| Worker 对象读取/校验失败 | 任务/文档失败 | 对象缺失、超限或损坏 | 不可见 | 修复对象后重新入库，不使用未校验正文 |
| embedding provider 失败 | `FAILED`，已完成向量可保留 | Qdrant 为上一代或无新点 | 不可见 | 重新入库复用已持久化向量 |
| Qdrant delete/upsert 失败 | `FAILED/INDEXING` | 上一代或局部新点 | 不可见 | 重复文档级发布，先清旧再写完整代 |
| 文档删除后 Qdrant/MinIO 清理失败 | SQL 文档已删除 | 孤儿点/对象 | 不可见 | V3/V4 修复账本重试 |
| KB 删除后 collection/对象清理失败 | SQL KB 已删除 | 孤儿 collection/对象 | 不可见 | 每个远端义务独立入账并重试 |
| RabbitMQ 在补偿调度时不可用 | SQL 删除成功、账本 `PENDING` | 孤儿远端资源 | 不可见 | 管理 API、Console 或后续 Worker 重放账本 |

## 4. 持久修复账本

Execution PostgreSQL V3 新增 `cross_store_repairs`；V4 增加 `payload_json`，支持：

- `QDRANT/DELETE_DOCUMENT_POINTS`；
- `QDRANT/DELETE_COLLECTION`；
- `MINIO/DELETE_OBJECT`。

账本记录存储、操作、租户、KB、文档、状态、尝试次数、payload、最近错误和完成时间。执行器用 `FOR UPDATE SKIP LOCKED` 领取，允许多 Worker 并发；失败行长期保留，可审计并再次执行。

- `GET /api/v1/admin/system/cross-store-repairs`：按状态查看；
- `GET /api/v1/admin/system/cross-store-repairs/stats`：积压、年龄、失败率、分存储统计和阈值健康等级；
- `GET /api/v1/admin/system/cross-store-repairs/metrics`：Prometheus 文本指标；
- `POST /api/v1/admin/system/cross-store-repairs/execute`：立即执行一批；
- `GET /api/v1/admin/system/object-storage/health`：MinIO bucket、版本化、生命周期和 TLS 的只读治理检查；
- `app.tasks.repair_cross_store`：后台指数退避，最多重试 8 次；
- Console 数据库页面：统计、筛选、错误查看、确认后手工执行。

执行前会回收超过配置时限的 `IN_PROGRESS` claim，将其标为 `FAILED` 后重新领取，覆盖 Worker/API 在远端操作期间崩溃的恢复窗口。

## 5. 验证证据

`services/execution-plane/tests/` 使用隔离 PostgreSQL schema、可注入故障的 Qdrant 双和真实/内存 MinIO，覆盖：

- Qdrant 局部 upsert 失败时不可检索，恢复后整代发布无重复点；
- 维度错配 fail closed，单文档错误不会删除整个 KB collection；
- MinIO 上传中断补偿，不投递不完整任务；
- 文档删除的 Qdrant/MinIO 义务及 KB 删除的 collection/多对象义务独立入账并恢复；
- 对象引用读取、大小限制、SHA-256 校验和真实 MinIO 生命周期。

当前基线：Execution 83/83、Console 23/23、端点目录 230/230；Stack（含并行 fork/join）、Outbox reliability、DLQ audit、Cross-store fault 四组 smoke 通过。真实停止 MinIO/Qdrant 时义务进入 `FAILED`、健康变为 `CRITICAL`，恢复后重试成功且物理资源消失；运行后 13/13 服务正常，队列、测试资源和未完成账本均为 0。

## 6. 尚未完成

- Qdrant collection 维度变更继续 fail closed，仍需显式 KB 级重建流程；
- 已覆盖真实 Qdrant/MinIO 停止与恢复，仍需磁盘满及大文件/并发压力测试；
- 已提供积压数量、最老记录年龄、失败率、阈值健康等级、统一告警预览、Console 总览和 Prometheus 文本指标；仍需接入外部 Alertmanager/Webhook；
- MinIO bucket 生命周期、版本化和 TLS 已提供只读治理检查，实际策略、加密和备份恢复仍需按生产保留策略配置；
- PostgreSQL `raw_text` 兼容副本尚未按阈值卸载。

MinIO 对象协议详见 `docs/design/document-object-storage-minio-v1.zh-CN.md`。
