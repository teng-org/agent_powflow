# retrieval

检索与向量接口实现。

## 当前实现

- `schemas.py`：入库与检索请求模型。
- `store.py`：PostgreSQL（文档/chunk/task/object 元数据与修复账本）+ Qdrant（向量索引）
- `runtime/object_storage.py`：MinIO 原件/解析文本、SHA-256 校验、读取上限与安全对象 key
- `service.py`：文档切分、embedding 索引、向量相似度检索（cosine）
- embedding 来源：`models/embedding_provider.py`
  - 优先 SiliconFlow `/v1/embeddings`
  - 失败降级到本地 deterministic embedding

## 运行依赖

- PostgreSQL
- Qdrant
- MinIO

文档索引采用 PostgreSQL 发布状态屏障与文档级 Qdrant 整代替换；原件和解析文本写入 MinIO，新 Celery 任务只传对象引用与校验和。文档/KB 删除后的 Qdrant 或 MinIO 清理失败会写入 `cross_store_repairs`，由 Worker、管理 API 或 Console 重试。MongoDB 不参与文档权威写链路。

环境变量：

- `POSTGRES_DSN`
- `QDRANT_URL`
- `QDRANT_API_KEY`（可选）
- `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_DOCUMENT_BUCKET`
- `MINIO_SECURE`
- `OBJECT_STORAGE_MAX_READ_BYTES`（默认 64 MiB）
- `UPLOAD_MAX_FILES_PER_REQUEST` / `UPLOAD_MAX_BATCH_BYTES`
- `INGEST_MAX_TEXT_BYTES`（默认 10 MiB，按 UTF-8 字节限制 JSON 文本入库）
- `OBJECT_STORAGE_TENANT_QUOTA_BYTES`（默认 0；生产必须设置正整数）
- `OBJECT_STORAGE_QUOTA_RESERVATION_TTL_SECONDS`（默认 300 秒）
- `OBJECT_STORAGE_QUOTA_WARN_RATIO`（默认 0.8）
- `OBJECT_STORAGE_CAPACITY_TENANT_LIMIT`（默认 100）
- `CROSS_STORE_REPAIR_STALE_AFTER_SECONDS`（默认 300 秒）
- `CROSS_STORE_REPAIR_WARN_OPEN_COUNT` / `CROSS_STORE_REPAIR_CRITICAL_OPEN_COUNT`
- `CROSS_STORE_REPAIR_WARN_AGE_SECONDS` / `CROSS_STORE_REPAIR_CRITICAL_AGE_SECONDS`

管理面接口：

- `/api/v1/admin/system/cross-store-repairs/stats`：修复积压与健康等级；
- `/api/v1/admin/system/cross-store-repairs/metrics`：跨存储修复及对象容量 Prometheus 文本指标；
- `/api/v1/admin/system/object-storage/health`：只读检查 MinIO bucket、版本化、生命周期、TLS 及租户逻辑容量；
- `/api/v1/admin/system/alerts/preview`：返回 `cross_store_repair` 与 `object_storage_capacity` 统一告警。

外部 Alertmanager/Webhook 与实际 bucket 生命周期、版本化和 TLS 策略仍需按生产环境要求部署。

## 数据库迁移

API 与 Worker 启动前都会执行 `python -m retrieval.schema_migrations`。运行时 Store 只校验 `execution_schema_migrations` 是否达到当前版本，不再在请求路径隐式建表。迁移失败、版本过新或已成功版本发生 checksum 漂移时服务拒绝启动。

手工检查或升级：

```powershell
cd services/execution-plane
$env:POSTGRES_DSN = 'postgresql://apf:apf@localhost:5432/agentpowflow'
python -m retrieval.schema_migrations
```

新增 schema 变更必须追加版本，不得修改已执行版本；详见 `docs/design/execution-postgresql-schema-migrations-v1.zh-CN.md`。

当前迁移为 V1–V11：V9 增加批次取消计数和父 Task 索引，V10 增加 RAG 2.0 索引修订清单、Chunk 词法特征与 GIN 索引，V11 增加 Content Block 元数据、Parent/Child 持久化表与父块回填索引。旧 Chunk 可在迁移完成后运行 `python -m retrieval.rag2_backfill` 分批升级，不重新计算 Dense embedding。对象协议、删除补偿及容量治理详见 `docs/design/document-object-storage-minio-v1.zh-CN.md`、`docs/design/object-storage-capacity-governance-v1.zh-CN.md` 与 `docs/design/minio-physical-capacity-observability-v1.zh-CN.md`。

## 持久化任务不变量

- `task_id` 的 tenant、KB、document 与任务类型在首次创建后不可变；同身份重复创建幂等，不重置状态或进度。
- `SUCCEEDED`、`FAILED`、`CANCELLED` 为不可逆终态；进度只允许单调增加，终态后不再更新。
- 任务查询、事件和取消均支持 tenant 过滤；事件写入必须匹配任务的权威身份。
- Celery 文档任务重投递会复用权威记录，终态或已取消任务不会被重新打开。

真实 PostgreSQL 集成测试要求显式设置测试 DSN：

```powershell
$env:EXECUTION_TEST_POSTGRES_DSN = 'postgresql://apf:apf@localhost:5432/agentpowflow'
$env:EXECUTION_TEST_MINIO_ENDPOINT = 'localhost:9000'
$env:EXECUTION_TEST_MINIO_ACCESS_KEY = 'minioadmin'
$env:EXECUTION_TEST_MINIO_SECRET_KEY = 'minioadmin'
python -m unittest discover -s services/execution-plane/tests -p 'test_*.py' -v
```

测试为每个用例创建并严格清理独立的 `apf_it_<32 hex>` schema；真实 MinIO 测试使用唯一 `apf-it-*` bucket 并在 `finally` 中严格清理。详细契约见 `docs/design/execution-task-store-invariants-integration-tests-v1.zh-CN.md`。
