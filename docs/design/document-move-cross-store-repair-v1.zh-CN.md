# 文档移动跨存储持久修复 v1

- 日期：2026-08-08
- 阶段：Task Authority Phase 2G
- 状态：已实现并完成真实 Qdrant 停止/恢复验收

## 1. 审计发现

原移动实现先把文档、分块和草稿从源 KB 写入目标 KB并删除源 SQL 记录，然后同步重建目标 Qdrant points、删除源 points。SQL 使用自动提交连接，远端 Qdrant 失败时存在三个问题：

1. PostgreSQL 已经完成逻辑移动，但 Task 标记失败；
2. 目标 Qdrant 可能只有部分 points，而目标文档仍保持 `INDEXED`，存在部分结果可见窗口；
3. 重试仍按源 KB 查文档，因源记录已删除而返回“document is missing”，无法自行收敛。

MinIO 对象在移动时不复制、不改 bucket/key，只迁移 PostgreSQL 中的引用，因此移动没有 MinIO 远端写步骤；修复必须保证引用原样保留。

## 2. 新的一致性协议

移动现在分为逻辑提交和物理对账：

1. 对源/目标 Qdrant collection 取得排序后的 PostgreSQL advisory transaction locks；
2. 在一个 PostgreSQL 事务中复制文档/分块/草稿、删除源记录，并插入 `QDRANT / MOVE_DOCUMENT_POINTS` repair；
3. 目标文档状态先写为 `MOVING`，原状态记录在 repair `payload_json.original_status`；
4. repair 幂等删除目标旧 points、按目标 KB payload 完整写入当前 generation，再删除源 points；
5. 全部远端操作成功后，只有仍为 `MOVING` 的目标文档才恢复原状态；
6. 任一步失败，repair 进入 `FAILED`，目标文档继续不可检索；重复执行使用同一 SQL generation，可安全覆盖部分写；
7. 重提同一移动命令时，Execution dispatch 若发现源文档缺失，会要求目标文档和匹配的 move repair 同时存在，随后允许 Worker 进入幂等收敛路径。

该协议以 PostgreSQL 为逻辑权威，以 `MOVING` 为发布屏障；不会尝试对 PostgreSQL、Qdrant、MinIO 做不可实现的分布式事务。

## 3. 运营接口与 Console

新增：

- `POST /api/v1/admin/system/cross-store-repairs/{repair_id}/execute`

接口只执行指定 repair，平台管理员权限不变。不存在返回 404；已完成 repair 幂等返回成功；被其他执行器领取时返回 pending。原批量接口继续用于有界后台治理。

Console 数据库监控页现在：

- 将 `MOVE_DOCUMENT_POINTS` 显示为“移动文档向量对账”；
- 展示源 KB、目标 KB、文档 ID 和应恢复的发布状态；
- 对 `PENDING/FAILED` 行提供“重试此项”，不会顺带执行其他租户或资源的 repair；
- 保留批量 50 条操作用于明确确认后的全局治理。

## 4. 自动与真实验证

故障注入集成测试使用可在 upsert 第一个 point 后失败的 Qdrant double，证明：

- SQL 逻辑移动和 repair 同时存在；
- 目标状态为 `MOVING`；
- 源/目标查询均不返回部分数据；
- repair 记录包含精确 source/target/original status；
- 重试后目标完整、源清零、状态恢复 `INDEXED`；
- MinIO source/extracted key 完全不变。

真实脚本 `tools/smoke/document-move-repair-smoke.ps1`：

1. 创建临时源/目标 KB并完成 Control-owned 文本入库；
2. 真实停止 Qdrant 后提交 Control 移动命令，Task 必须失败且错误包含 repair identity；
3. 重启 Qdrant，验证源 SQL 已无文档、目标状态为 `MOVING`、目标 points 为 0；
4. 通过单条 repair API 恢复 2 个目标 points、源 points 为 0、目标恢复 `INDEXED`；
5. 再次提交相同移动命令，新 Task 成功；
6. 删除临时 KB 和本次 repair 审计行，PostgreSQL/Qdrant/MinIO 全版本严格清零。

最终回归：Execution 155/155（真实隔离 PostgreSQL + TLS MinIO，0 跳过）、Control→Execution 契约 14/14、Console 55/55 并通过生产构建、端点目录 288/288。

## 5. 剩余边界

- 原失败 Task 保留为失败审计事实；物理 repair 成功后，业务方可重提同一移动命令生成新的成功 Task，系统不篡改历史终态；
- repair ledger 当前长期保留成功记录，正式环境需要独立的保留/归档策略；
- 本次覆盖单节点 Qdrant 停止和部分 upsert，尚未覆盖 Qdrant 多节点分区、长时间持续饱和和备份恢复期间的并发移动；
- 下一优先级转向真实第三方 Provider 契约与生产级多节点/容量故障演练。
