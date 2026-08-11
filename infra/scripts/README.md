# scripts

初始化、迁移、运维与恢复验证脚本。

## 备份恢复

- `verify-postgres-backup-restore.ps1`：custom archive、隔离数据库恢复、表/迁移账本/约束核对；
- `verify-mongodb-backup-restore.ps1`：gzip archive、隔离 namespace 恢复、集合与文档计数核对；
- `verify-qdrant-snapshot-restore.ps1`：canary collection snapshot 上传恢复与 point/payload 核对；
- `verify-minio-backup-restore.ps1`：版本化文档 bucket 隔离前缀的多版本归档、恢复与逐版本校验；
- `verify-unified-backup-set.ps1`：顺序编排上述四项并生成统一、二次校验的 backup set manifest。

统一 backup set 是 `online_fuzzy_window`，不是跨存储原子快照。整组恢复必须静默写入并执行跨存储一致性核对，详见 `docs/operations/unified-backup-set-drill-v1.zh-CN.md`。
