# PostgreSQL 备份与恢复演练手册 v1

## 1. 目标与边界

当前 Control Plane、Execution Plane、Chat Plane 共用 PostgreSQL 数据库 `agentpowflow`。本手册覆盖该数据库的完整逻辑备份与隔离恢复验证，不覆盖 MongoDB、MinIO、Qdrant、RabbitMQ 的数据保护；这些存储仍需分别制定备份与灾难恢复方案。

脚本使用 PostgreSQL custom archive：

1. 在 PostgreSQL 容器内执行 `pg_dump --format=custom`；
2. 先用 `pg_restore --list` 校验归档可读取，再复制到工作区；
3. 创建随机命名的隔离数据库并执行完整恢复；
4. 校验 public 表数量、三套迁移账本、关键业务表和外键有效性；
5. 删除隔离数据库和容器内临时归档，保留主机备份与校验清单。

演练不会删除或覆盖运行库，也不会重建 PostgreSQL volume。

## 2. 执行方式

前提：完整 Docker Compose 栈已启动，`agentpowflow-postgres` 处于 running 状态。

```powershell
.\infra\scripts\verify-postgres-backup-restore.ps1
```

默认输出到 `data/backups/postgres/`（已加入 `.gitignore`）。也可指定工作区内的其他目录：

```powershell
.\infra\scripts\verify-postgres-backup-restore.ps1 -OutputDirectory data/backups/manual
```

为避免误写和误删，输出目录必须位于当前工作区内。隔离库名和容器临时文件名均使用脚本内部生成的 32 位随机标识，并在清理前再次校验格式。

## 3. 成功标准

命令必须输出 `POSTGRES_BACKUP_RESTORE_OK`，且同时满足：

- custom archive 非空且 `pg_restore --list` 可读取；
- 恢复库与源库的 public 基础表数量一致；
- `flyway_schema_history`、`execution_schema_migrations`、`chat_schema_migrations` 的最高版本与记录数一致；
- Run、人工审批、文档和 Chat 等关键表存在；
- 没有未验证的外键约束；
- 隔离恢复库和容器临时归档均已清理。

每次成功执行会保留：

- `*.dump`：custom-format 备份；
- `*.manifest.json`：SHA-256、字节数、归档对象数、表数量、迁移账本和清理结果。

## 4. 恢复到正式环境前的要求

该脚本只验证备份可恢复，不直接恢复正式环境。正式灾难恢复必须先：

1. 冻结写流量并记录事故时间点；
2. 在隔离 PostgreSQL 实例中复验归档和 manifest 的 SHA-256；
3. 明确 RPO/RTO、数据缺口及 MongoDB/MinIO/Qdrant 的一致性处理；
4. 由双人复核目标实例与目标数据库，另行执行切换；
5. 恢复后运行 Stack Smoke、跨存储一致性检查和业务抽样核对。

## 5. 当前限制与后续项

- 这是逻辑全量备份，不是持续归档或时间点恢复（PITR）；生产环境仍需启用 WAL 归档、加密异地存储、保留策略和定期恢复演练。
- 备份发生时应避免并发 DDL/迁移；普通事务写入由 `pg_dump` 一致性快照处理。
- 备份文件当前保留在本机工作区，不等同于异地、不可变或加密备份。
- MongoDB、MinIO、Qdrant 必须建立各自的备份、恢复和跨存储一致性编排。
