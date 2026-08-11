# Chat PostgreSQL 显式 Schema Migration v1

## 背景与结论

Chat Plane 过去由 `ChatStore._ensure_pg_table()` 和 `ChatApiPoolManager._pg_ensure_schema_locked()` 在加载数据或普通请求路径执行 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE`。这种方式没有版本账本、已执行内容校验、失败留痕或多实例互斥，也会在 schema 不兼容时静默回退本地 JSON。

当前所有 PostgreSQL DDL 已收口到 `runtime.schema_migrations`。容器在 Uvicorn 启动前强制执行迁移；业务 Store 只校验迁移版本，不再创建或修改数据库对象。缺少迁移、迁移未完成、数据库版本过新或 checksum 漂移时均 fail closed。

## 版本序列

- V1 `chat_core_store`：快照、会话、标题元数据、消息、会话记忆、用户画像和基础记忆项。
- V2 `chat_memory_runtime`：记忆项生命周期字段、会话摘要、近期活动、融合日志、会话片段、Chat Run、记忆重排任务、Chat API Pool 状态和查询索引。

迁移 SQL 使用幂等建表/加列语句，因此可以接管旧版运行时已创建的表，并保留既有数据。版本一旦成功登记，其名称、顺序和 SQL checksum 不得修改；后续变更只能追加连续版本。

## 迁移账本与并发

`chat_schema_migrations` 保存版本、名称、SHA-256 checksum、成功状态、开始/完成时间、耗时和失败原因。迁移前获取 Chat 专用 PostgreSQL session advisory lock，多个实例同时启动时只有一个执行 DDL。

迁移器拒绝以下状态：

1. 已成功版本的名称或 checksum 与代码不一致；
2. 数据库包含高于当前运行时代码的版本；
3. 迁移注册表版本不连续、重名或 checksum 非法；
4. 账本缺失或存在未成功版本时业务 Store 尝试启动。

## 启动与运维

显式执行：

```powershell
$env:POSTGRES_DSN = 'postgresql://apf:apf@localhost:5432/agentpowflow'
Set-Location services/chat-plane
python -m runtime.schema_migrations
```

成功输出 `CHAT_SCHEMA_MIGRATED ...` 或 `CHAT_SCHEMA_CURRENT version=2`；失败输出 `CHAT_SCHEMA_MIGRATION_FAILED` 并返回非零状态。Chat 镜像的启动命令采用 `migration && exec uvicorn`，迁移失败时不会开放 API。

发布顺序：先备份并在生产快照副本验证迁移，再部署 Chat 镜像。当前 Control、Execution 和 Chat 共用 `public` schema，但各自使用独立迁移账本和 advisory lock；长期仍应按平面拆分 schema/database。回滚以向前修复或数据库备份恢复为准，不自动执行 down migration。

## 自动验证

`ChatSchemaMigrationIntegrationTest` 使用严格命名的 `apf_chat_it_<32 hex>` 隔离 schema，并在清理前再次校验名称，覆盖：

- 空 schema 执行 V1/V2及重复执行幂等；
- V1→V2 升级并保留既有数据；
- 旧版运行时表的无损接管；
- 未迁移 Store/API Pool fail closed 且不创建业务表；
- checksum 漂移和未来版本拒绝；
- 失败账本留痕、非法注册表拒绝；
- 两个迁移器并发时每个版本仅执行一次。

CI 提供 PostgreSQL 16，先执行生产迁移入口，再运行 Chat 全部测试。当前 Chat 基线为 31/31，其中 9 项覆盖迁移，8 项覆盖 Run/SSE 生命周期，6 项覆盖真实 HTTP Provider 故障沙箱。
