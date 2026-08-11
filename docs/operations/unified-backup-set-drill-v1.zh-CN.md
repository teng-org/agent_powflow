# 四存储统一备份集恢复演练 v1

## 1. 目标与边界

`infra/scripts/verify-unified-backup-set.ps1` 统一编排 PostgreSQL、MongoDB、Qdrant 与 MinIO 四项隔离恢复演练。每个组件必须先生成备份、恢复到隔离目标、核对业务库存或 canary 数据、完成清理，再进入统一 backup set；总 manifest 会再次核对组件归档的 SHA-256。

该脚本是在线演练，四个组件顺序取样，因此一致性模型明确记录为 `online_fuzzy_window`，`atomic_cross_store_snapshot=false`。它证明“每个组件的备份可读且能恢复”，不证明四类存储处在同一个原子业务时点。真实整组恢复必须先停止写入，并在恢复后运行跨存储修复与一致性核对。

## 2. MinIO 版本语义

MinIO 演练使用已由治理身份创建并启用 versioning 的文档 bucket，应用身份只在 `v1/system/backup-restore-drills/<随机 ID>/` 下写入隔离前缀：

- 3 个逻辑对象，共 5 个物理版本；
- 归档内保存版本顺序、每版字节数与 SHA-256；
- 恢复时按旧到新顺序重新创建版本，并逐版本复读校验；
- finally 按具体 version ID 删除源/恢复前缀，随后再次列举并要求残留为 0。

演练不扩大应用身份权限：建 bucket、修改 bucket versioning 与生命周期仍只属于治理身份。

## 3. 执行

```powershell
.\infra\scripts\verify-unified-backup-set.ps1
```

输出目录必须位于工作区内。成功目录包含一个 `backup-set.manifest.json` 和四个组件子目录。总 manifest 记录备份时间窗、组件归档相对路径、大小、校验和、隔离恢复结果及恢复前置条件，不记录数据库或对象存储密码。

## 4. 生产关闭条件

本演练之后仍需在专用环境完成：

1. 写入静默或业务 checkpoint 协议，使四存储形成可声明的恢复点；
2. PostgreSQL WAL/PITR、跨地域复制及不可变/加密归档；
3. MinIO 多节点纠删码、站点复制和对象锁保留策略；
4. Qdrant 多副本 snapshot/恢复和分区故障；
5. 明确 RPO/RTO，定期自动演练并把结果送入外部值班渠道。
