# Execution PostgreSQL 显式 Schema Migration v1

## 背景与目标

Execution Plane 过去由 `KnowledgeBaseStore._ensure_indexes()` 和 API Pool Store 在普通请求或 bootstrap 路径执行 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE`。这会让应用实例兼任迁移器，无法回答“当前 schema 版本是什么、哪次升级失败、代码是否修改了已执行迁移”，多 Worker 并发启动时也缺少明确互斥。

当前实现将所有 Execution PostgreSQL DDL 执行入口收口到 `retrieval.schema_migrations`。API 与 Worker 在启动业务进程前显式运行迁移，并在应用初始化时再次验证；Store 的 `_ensure_indexes()` / `_ensure_schema()` 只验证版本，数据库未迁移、迁移失败、版本过新或校验和漂移时全部 fail closed。

## 版本

| 版本 | 名称 | 内容 |
| --- | --- | --- |
| V1 | `execution_store_baseline` | KB、document、chunk、task、配置、用量、告警等原有 Execution Store 基线 |
| V2 | `api_pool_tables` | 主 API Pool 与 AUX API Pool 的 settings/endpoints 四张表 |
| V3 | `cross_store_repair_ledger` | PostgreSQL/Qdrant 删除补偿义务、状态、尝试次数和错误账本 |
| V4 | `document_object_storage_metadata` | 文档 MinIO 原件/解析文本定位、SHA-256、大小/类型，以及修复 payload |
| V5 | `object_storage_capacity_reservations` | 解析对象字节数、租户在途容量预留表和租户/过期索引 |
| V6 | `object_storage_capacity_backfill` | 使用 `octet_length(raw_text)` 回填已有解析对象的 UTF-8 字节数 |
| V7 | `alertmanager_alert_facts` | Alertmanager firing/resolved 事实、标签/注解快照、恢复时间与状态索引 |
| V8 | `task_state_authority_owner` | Task 增加非空 `state_owner`；Execution 兼容任务默认归属 `EXECUTION`，Control 创建的任务保留 `CONTROL` |
| V9 | `task_batch_cancelled_items` | 批次增加非空取消计数，并增加按租户/父 Task 查询索引 |
| V10 | `rag2_index_manifest_and_lexical_features` | RAG 2.0 索引修订清单、Chunk 词法特征、GIN 索引与版本标识 |

V1 将原有幂等 DDL 作为一次性基线迁移，并在单个数据库事务中执行；后续版本使用独立事务。不得修改已发布迁移内容，只能追加新版本。

## 权威迁移账本

`execution_schema_migrations` 保存：

- `version`、`name`、SHA-256 `checksum`；
- `success`、`started_at`、`applied_at`；
- `execution_ms` 与截断后的 `error_message`。

执行成功的版本若名称或校验和与代码不一致，启动直接失败。数据库存在高于当前代码的版本时也直接失败，防止旧镜像连接新 schema。

迁移前获取固定 PostgreSQL session advisory lock。API 和 Worker 同时启动时只有一个实例执行 DDL，其余实例等待后重新读取账本；并发集成测试验证每个版本只执行并登记一次。

## 启动与运维

手工执行：

```powershell
cd services/execution-plane
$env:POSTGRES_DSN = 'postgresql://apf:apf@localhost:5432/agentpowflow'
python -m retrieval.schema_migrations
```

输出只有两类成功结果：

```text
EXECUTION_SCHEMA_MIGRATED versions=1,2,3,4,5,6,7,8,9,10 current=10
EXECUTION_SCHEMA_CURRENT version=10
```

失败时进程返回非零退出码，并输出 `EXECUTION_SCHEMA_MIGRATION_FAILED`。Docker API CMD 与 Compose Worker command 都采用 `migration && exec application`，因此迁移失败时不会启动 Uvicorn 或 Celery。

CI 在 Execution 测试前针对 PostgreSQL 16 service 显式执行同一迁移命令。隔离 Store/API 测试仍为每个测试类创建 `apf_it_<32 hex>` schema，并通过同一生产迁移器初始化，避免测试 schema 与生产路径分叉。

## 新增迁移规则

1. 在 `migrations()` 末尾追加连续版本，禁止重排、复用或删除已发布版本。
2. 将 DDL 放入稳定常量或不可变函数，并以其内容计算 SHA-256。
3. 每个版本自行使用单个事务；不要执行 `DROP DATABASE`、`DROP SCHEMA` 或依赖隐式搜索路径以外的对象。
4. 先写空 schema 升级、已有 schema 升级、幂等和失败留痕测试，再更新运行镜像。
5. 破坏性变更使用 expand/contract：先增加兼容结构，完成数据回填与双读/双写，再在后续版本删除旧结构。
6. 生产回滚以应用向前修复和数据库备份恢复为准，不自动执行 down migration。

## 验证基线

- Execution unittest：150/150（真实隔离 PostgreSQL + TLS MinIO，无跳过）；
- 迁移专项：空 schema、V1→V10、V5→V6 多字节正文回填、V7 告警事实表、V8 Task 所有权列、V9 批次取消计数/父索引、V10 RAG 索引清单与词法列、重复执行、并发执行、失败记录、注册表连续性、版本过新拒绝、checksum 漂移、未迁移 Store/API Pool fail closed；
- 公共开发库应顺序记录 V1–V10，API/Worker 启动输出 `EXECUTION_SCHEMA_CURRENT version=10`；
- Stack、Outbox reliability、DLQ audit 冒烟均通过。

## 未覆盖范围

本迁移器只负责 Execution Plane PostgreSQL 对象，目前为 V1–V10。V3 增加 `cross_store_repairs`，V4 将 MinIO 对象元数据和 Qdrant collection/MinIO object 补偿 payload 纳入显式 schema，V5/V6 增加并发容量预留和历史大小回填，V7 增加 Alertmanager 告警事实，V8 增加 Task 状态所有权字段，V9 增加批次取消计数和父 Task 索引，V10 增加 RAG 2.0 索引清单与词法检索特征。Control Plane 使用 Flyway 与 Hibernate validate，Chat Plane 使用独立显式迁移与启动门禁。Qdrant collection 和 MinIO bucket 自身的版本、生命周期、物理磁盘与备份策略仍需独立治理。
