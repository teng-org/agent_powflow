# 多存储备份与隔离恢复演练 v1

## 1. 覆盖范围

Agent PowFlow 的权威数据并不只存在于 PostgreSQL：

| 存储 | 当前职责 | v1 演练方式 |
| --- | --- | --- |
| PostgreSQL | 三平面权威业务状态、迁移账本、审批与审计 | 全库 custom archive，恢复到随机隔离数据库 |
| MongoDB | Execution 文档、chunk、task event 等事件/大字段数据 | 全库 gzip archive，namespace 重映射到随机隔离数据库 |
| Qdrant | 文档与 Chat 向量 | 随机 canary collection，原生 snapshot 下载并 upload 恢复 |
| MinIO | 文档原文件/对象版本 | 受治理文档 bucket 的随机隔离前缀，多版本归档、回传并逐版本校验 |

所有演练遵守非覆盖原则：运行数据库和业务 collection 不作为恢复目标；MinIO 只使用文档 bucket 下随机、专用的 canary 前缀，不覆盖业务 key。随机资源名或前缀均在删除前再次校验，成功/失败路径都执行清理。

## 2. 执行命令

完整 Docker Compose 栈启动后：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-postgres-backup-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-mongodb-backup-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-qdrant-snapshot-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-minio-backup-restore.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\verify-unified-backup-set.ps1
```

备份和 manifest 默认写入 `data/backups/<store>/`。该目录已被 Git 忽略，但仍位于本机工作区，不等同于异地备份。

## 3. 2026-08-03 实测证据

| 演练 | 结果 | 备份字节数 | SHA-256 |
| --- | --- | ---: | --- |
| PostgreSQL | stderr 兼容修复后复跑，70 张表、15,337 个 archive objects、三套迁移账本 8/4/2 一致 | 16,263,625 | `7e306839f89fd4dfde2b8159339658f1331b078b5ba406fdd39f9ee19314acd8` |
| MongoDB | 8 个集合、916 条文档逐集合一致 | 782,222 | `2019dff77eb5e6a0f485d67dfe18fa5cb412ac390ea204ac8779a3cd4eb8f4c7` |
| Qdrant | 开启 API Key 的临时实例中，无 Key 请求被拒绝；2 个 canary points 的 ID、向量集合与 payload 恢复成功 | 451,072 | `fcf62263b5f19f068a5378ab246248039ac790d5a0a277ce38097b3effb6a152` |
| MinIO | 3 个 canary objects、1,086 bytes payload 逐对象 SHA-256 一致 | 705 | `6db1fac4c1756d77fa1201b07b2841aff1d14d0d64b096d12ad8539736775dc2` |

演练后独立枚举确认：PostgreSQL/MongoDB `apf_restore_*` 数据库、Qdrant `apf-drill-*` collection、MinIO `apf-drill-*` bucket 和容器临时文件均为 0。

## 4. 一致性与恢复顺序

四个备份当前不是同一分布式快照，不能宣称任意时刻严格一致。正式事故恢复建议：

1. 冻结写流量和 Worker 消费；
2. 恢复 PostgreSQL 权威状态；
3. 恢复 MongoDB 对应事件与大字段；
4. 恢复 MinIO 对象和 Qdrant 向量；
5. 运行跨存储修复账本，重建缺失对象/向量；
6. 执行 Stack Smoke、业务抽样与 tenant 边界验证；
7. 记录 RPO 数据缺口后再恢复写流量。

## 5. 仍未解决的生产化问题

- PostgreSQL 尚未启用 WAL archive/PITR；MongoDB 当前为单节点，不能使用带 oplog 的一致性备份。
- Qdrant/MinIO 的 v1 演练验证恢复机制，但尚未自动遍历并备份全部业务 collection/bucket。
- 备份未自动加密、签名、上传异地不可变介质，也没有保留/过期策略。
- 已定义统一 backup set ID、组件 manifest 和二次校验，但尚无写入静默/跨存储原子 checkpoint，因此仍不能把四份在线顺序备份视为同一业务时点。
- 尚未建立备份失败、备份陈旧、容量不足和恢复演练过期的指标与告警。

在以上问题完成前，这套能力属于“可恢复机制已验证”，不是完整生产灾备系统。
